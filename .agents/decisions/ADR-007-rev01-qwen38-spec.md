# ADR-007: Qwen 3.8 LiveTranslate Protocol Verification (`REV-01`)

- Status: Accepted with one runtime `PARTIAL`
- Date: 2026-11-22
- Scope: R06 protocol gate only
- Decision owner: HARRY Translate V2 Mac

## Context

R06 must not invent language codes or workspace routing. Before changing the
engine, `[REV-01]` requires verifying the official Qwen LiveTranslate protocol
and a real account response.

## Official evidence

- Official page:
  `https://help.aliyun.com/zh/model-studio/qwen3-5-livetranslate-flash-realtime`
- Page last modified: 2026-11-19 12:05:01 +08:00
- Client event reference:
  `https://help.aliyun.com/zh/model-studio/live-translator-client-events`
- The page explicitly covers
  `qwen3.8-livetranslate-flash-realtime` and its WebSocket event contract.

## Verified protocol decisions

1. The Beijing private endpoint is
   `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime`.
   The Singapore form uses `ap-southeast-1`.
2. `WorkspaceId` is part of the URL host. It is not an Authorization header
   value and is not a `session.update` field in the direct WebSocket protocol.
   The API key and workspace must belong to the same region.
3. The target language is sent as `session.translation.language`.
4. `qwen3.8-livetranslate-flash-realtime` performs source-language detection
   automatically. The source language has no explicit wire field for this
   model. Consequently, local `auto` is a routing/UI value and must never be
   invented as a wire source-language parameter.
5. The official table marks `zh`, `en`, `fr`, `es`, `it`, `ja`, `ko`, and
   `de` as audio plus text output languages. These codes are valid
   `translation.language` values.
6. Text-only output uses `session.output_modalities=["text"]`.
7. Source text is carried by
   `conversation.item.input_audio_transcription.delta` and
   `conversation.item.input_audio_transcription.completed`. Translation text is
   carried by `response.text.delta` and `response.text.done`.
8. RPM limits WebSocket connection creation, not turns inside an existing
   connection. Reconnects count as new requests. A `1007` close with
   `Requests rate limit exceeded` requires reduced connection frequency and
   exponential backoff.

## Real account probe

The probe used the existing local secret without printing or storing its value.
It did not read the key into Git, logs, evidence files, or frontend state.

- Key shape verified only as private `sk-ws-*`: `true`
- Configured URL shape: `wss://` private `.maas.aliyuncs.com`
- Workspace placeholder in configured URL: `false`
- Separate workspace environment variable: not required for this configured URL
- Target language: `en`
- Session handshake: `session.update == 1`
- State reached: `ready`
- Audio input: 2.862 seconds of synthetic 16 kHz mono PCM16
- Server events observed: `engine_state` and six `subtitle` delta events
- Metering fields in business events: `0`
- Final subtitle within the 30-second observation window: not observed

Result: real connectivity, private route, target-language handshake, and
streaming translation events are `PASS`; final-sentence completion is
`PARTIAL`. The probe was not retried, in accordance with the plan's
"failure -> PARTIAL, no retry loop" rule.

## Implementation requirements

- Resolve private URLs from the key shape and configured workspace URL, but do
  not hardcode a private workspace hostname.
- Apply `WorkspaceId` only by host substitution when a template or separate
  workspace value is actually present.
- Accept `auto` and the eight local source values for routing, but for Qwen 3.8
  send only the target language and rely on server-side source detection.
- Keep one `session.update` per successful connection.
- Ignore `response.done.usage` completely and never surface metering fields.
- Use the shared limiter for connection creation and exponential backoff for
  reconnect attempts.

## Rejected alternatives

- Sending `source_language=auto` as a Qwen 3.8 `session.update` field: the
  official model contract uses automatic source detection and has no such
  field.
- Putting `WorkspaceId` in a fake header: the official direct-WS contract uses
  it in the host.
- Treating the partial finalization result as a full pass: the evidence is
  recorded as `PARTIAL`.
- Retrying the paid probe until finalization appears: prohibited by the
  round-level real-API gate.
