import os
import sys
import json
import time
import base64
import asyncio
import threading
import queue
import pyaudio
import websocket
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

# 加载 .env 环境变量
load_dotenv()

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
QWEN_REALTIME_WS_URL = os.getenv(
    "QWEN_REALTIME_WS_URL",
    "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3.8-livetranslate-flash-realtime"
)
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "3200"))

main_loop = None
should_run = True

# 客户端分组连接池：彻底隔离 /ws/en 与 /ws/zh
connected_clients = {
    "en": set(),
    "zh": set(),
}

async def broadcast_to_channel(channel: str, data: dict):
    """向指定通道的前端大屏广播字幕（带超时与异常保护，防事件循环挂起）"""
    clients = connected_clients.get(channel)
    if not clients:
        return
    message = json.dumps(data, ensure_ascii=False)
    dead_sockets = set()
    for ws in list(clients):
        try:
            await asyncio.wait_for(ws.send_text(message), timeout=1.0)
        except Exception:
            dead_sockets.add(ws)
    for ws in dead_sockets:
        clients.discard(ws)

class QwenRealtimeChannel:
    """独立的 Qwen 实时同传会话通道，生命周期、重连与计费统计完全解耦"""
    def __init__(self, name: str, target_lang: str, label: str):
        self.name = name
        self.target_lang = target_lang
        self.label = label
        # 专属音频队列（解耦麦克风线程，防网络背压阻塞拾音）
        self.audio_queue = queue.Queue(maxsize=50)
        self.ws_client = None
        self.is_ready = threading.Event()

        # 句子状态追踪
        self.sentence_id = 0
        self.source_text = ""
        self.target_text = ""
        self.state_lock = threading.Lock()

        # Token 消耗与计费统计（官方单价：音频输入 40元/百万Token，文本输出 100元/百万Token）
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.cost_rmb = 0.0

    def reset_stats(self):
        with self.state_lock:
            self.input_tokens = 0
            self.output_tokens = 0
            self.total_tokens = 0
            self.cost_rmb = 0.0

    def dispatch(self, sentence_id: int, source: str, target: str, is_final: bool):
        """跨线程向本通道的前端大屏安全投递字幕帧与用量数据"""
        global main_loop
        if main_loop and main_loop.is_running():
            payload = {
                "channel": self.name,
                "id": sentence_id,
                "source": source,
                "target": target,
                "is_final": is_final,
                "usage": {
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "total_tokens": self.total_tokens,
                    "cost_rmb": round(self.cost_rmb, 4)
                }
            }
            asyncio.run_coroutine_threadsafe(
                broadcast_to_channel(self.name, payload),
                main_loop
            )

    def on_open(self, ws):
        print(f">> [{self.label}] WebSocket 连接建立，正在初始化会话 (目标语言: {self.target_lang})...")
        config = {
            "type": "session.update",
            "session": {
                "output_modalities": ["text"],
                "translation": {
                    "language": self.target_lang
                }
            }
        }
        ws.send(json.dumps(config))

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
        except Exception:
            return

        event_type = data.get("type", "")

        if event_type == "session.created":
            pass
        elif event_type == "session.updated":
            print(f">> [{self.label}] 会话初始化完成！正在监听中...")
            self.is_ready.set()
        elif event_type == "input_audio_buffer.speech_started":
            with self.state_lock:
                self.sentence_id += 1
                self.source_text = ""
                self.target_text = ""
                self.dispatch(self.sentence_id, "", "", False)
        elif "input_audio_transcription" in event_type:
            delta = data.get("delta") or data.get("text") or data.get("stash") or ""
            transcript = data.get("transcript")
            with self.state_lock:
                if transcript:
                    self.source_text = transcript
                elif delta:
                    self.source_text += delta
                self.dispatch(self.sentence_id, self.source_text, self.target_text, False)
        elif event_type in ("response.text.delta", "response.audio_transcript.delta"):
            delta = data.get("delta") or data.get("text") or ""
            with self.state_lock:
                if delta:
                    self.target_text += delta
                self.dispatch(self.sentence_id, self.source_text, self.target_text, False)
        elif event_type in ("response.text.done", "response.audio_transcript.done"):
            text = data.get("text") or data.get("transcript")
            with self.state_lock:
                if text:
                    self.target_text = text
                self.dispatch(self.sentence_id, self.source_text, self.target_text, False)
        elif event_type == "response.done":
            # 提取本轮精确 Token 账单
            usage = data.get("response", {}).get("usage") or data.get("usage") or {}
            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            tot_tok = usage.get("total_tokens", in_tok + out_tok)

            with self.state_lock:
                self.input_tokens += in_tok
                self.output_tokens += out_tok
                self.total_tokens += tot_tok
                # 官方计费：音频输入 40元/百万Token，文本输出 100元/百万Token
                self.cost_rmb = (self.input_tokens * 0.00004) + (self.output_tokens * 0.0001)

                print(f"[{self.label} 定稿] 原文: {self.source_text}")
                print(f"[{self.label} 定稿] 译文: {self.target_text}")
                print(f"[{self.label} 消耗] +{tot_tok} Token | 累计: {self.total_tokens} Token (约 ¥{self.cost_rmb:.4f})")
                self.dispatch(self.sentence_id, self.source_text, self.target_text, True)
        elif event_type == "error":
            print(f">> [{self.label} 错误] {data}")

    def on_error(self, ws, error):
        print(f">> [{self.label} 通道错误] {error}")

    def on_close(self, ws, code, msg):
        print(f">> [{self.label} 通道关闭] code={code}, msg={msg}")
        self.is_ready.clear()

    def run_ws(self):
        """独立的 WebSocket 运行线程，具备独立重连循环"""
        global should_run
        while should_run:
            if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY.startswith("在此填入"):
                print(f">> [{self.label} 警告] 未配置有效的 DASHSCOPE_API_KEY，请检查 .env 文件。")
                time.sleep(5)
                continue

            headers = ["Authorization: Bearer " + DASHSCOPE_API_KEY]
            self.ws_client = websocket.WebSocketApp(
                QWEN_REALTIME_WS_URL,
                header=headers,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close
            )
            try:
                self.ws_client.run_forever()
            except Exception as e:
                print(f">> [{self.label} 连接异常] {e}")

            if should_run:
                print(f">> [{self.label}] 3秒后尝试重新连接...")
                time.sleep(3)

    def run_audio_sender(self):
        """专属音频消费线程：从队列读取并向 WebSocket 推流"""
        global should_run
        while should_run:
            try:
                b64_audio = self.audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if self.is_ready.is_set() and self.ws_client and self.ws_client.sock and self.ws_client.sock.connected:
                try:
                    event = {
                        "type": "input_audio_buffer.append",
                        "audio": b64_audio
                    }
                    self.ws_client.send(json.dumps(event))
                except Exception:
                    pass
            self.audio_queue.task_done()

    def push_audio(self, b64_audio: str):
        """非阻塞压入音频帧，队列满时丢弃最旧数据，绝不阻塞麦克风采样"""
        if self.audio_queue.full():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except Exception:
                pass
        try:
            self.audio_queue.put_nowait(b64_audio)
        except Exception:
            pass


# 初始化两个独立通道：通道 EN（目标语言为英文）与通道 ZH（目标语言为中文）
channel_en = QwenRealtimeChannel("en", "en", "中译英-英文屏")
channel_zh = QwenRealtimeChannel("zh", "zh", "英译中-中文屏")
active_channels = [channel_en, channel_zh]


def audio_capture_worker():
    """后台采集 Mac 麦克风音频流并扇出分发给各个独立通道的推流队列"""
    global should_run
    p = pyaudio.PyAudio()
    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE
        )
        print(">> [麦克风] 麦克风录音通道已打开，正在持续采样并扇出至双通道...")
    except Exception as e:
        print(f">> [麦克风错误] 无法打开麦克风: {e}")
        print(">> 提示：请前往 Mac [系统设置] -> [隐私与安全性] -> [麦克风] 检查是否允许终端访问。")
        return

    while should_run:
        try:
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            b64_audio = base64.b64encode(data).decode("ascii")
            # 扇出分发（非阻塞投递）
            for ch in active_channels:
                ch.push_audio(b64_audio)
        except Exception:
            time.sleep(0.02)

    try:
        stream.stop_stream()
        stream.close()
        p.terminate()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global main_loop
    main_loop = asyncio.get_running_loop()
    print(">> [服务启动] 双通道同传系统已就绪！")
    print("   • 主控面板:    http://localhost:8000/")
    print("   • 中译英大屏:  http://localhost:8000/en  (投屏给外宾看)")
    print("   • 英译重大屏:  http://localhost:8000/zh  (投屏给中方看)")
    print("   • 左右双栏屏:  http://localhost:8000/split (现场控台对照)")
    yield


app = FastAPI(title="会议实时双语同传系统 (双通道)", lifespan=lifespan)


@app.websocket("/ws/{channel}")
async def websocket_channel_endpoint(websocket: WebSocket, channel: str):
    """分组 WebSocket 广播端点：客户端按需连接 /ws/en 或 /ws/zh"""
    if channel not in connected_clients:
        await websocket.close(code=1003)
        return

    await websocket.accept()
    connected_clients[channel].add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connected_clients[channel].discard(websocket)


@app.get("/api/stats")
def get_stats():
    """实时查询双通道 Token 消耗量与花费预估"""
    tot_in = sum(ch.input_tokens for ch in active_channels)
    tot_out = sum(ch.output_tokens for ch in active_channels)
    tot_tok = sum(ch.total_tokens for ch in active_channels)
    tot_cost = sum(ch.cost_rmb for ch in active_channels)
    return JSONResponse(content={
        "total_tokens": tot_tok,
        "input_tokens": tot_in,
        "output_tokens": tot_out,
        "cost_rmb": round(tot_cost, 4),
        "channels": {
            ch.name: {
                "input_tokens": ch.input_tokens,
                "output_tokens": ch.output_tokens,
                "total_tokens": ch.total_tokens,
                "cost_rmb": round(ch.cost_rmb, 4)
            } for ch in active_channels
        }
    })


@app.post("/api/stats/reset")
def reset_stats():
    """重置/清零本次会议 Token 统计"""
    for ch in active_channels:
        ch.reset_stats()
    return JSONResponse(content={"status": "ok"})


@app.get("/")
def get_dashboard():
    """主控面板 / 导航页"""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/en")
def get_en_screen():
    """中译英专属大屏"""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/zh")
def get_zh_screen():
    """英译中专属大屏"""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/split")
def get_split_screen():
    """双栏对比监看屏"""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


def main():
    # 启动每个通道的 WebSocket 维护线程与专属推流线程
    for ch in active_channels:
        threading.Thread(target=ch.run_ws, daemon=True).start()
        threading.Thread(target=ch.run_audio_sender, daemon=True).start()

    # 启动 Mac 麦克风录音采集线程
    threading.Thread(target=audio_capture_worker, daemon=True).start()

    # 启动本地 FastAPI Web 服务
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
