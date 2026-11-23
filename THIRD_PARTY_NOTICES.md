# Third-Party Notices

This file records the third-party packages and reused source distributions
currently used by the Apple Silicon Mac build. MIT and Apache-2.0 source
notices used by the release are reproduced below.

## Runtime Dependencies

| Component | Version | License | Use |
| :--- | :--- | :--- | :--- |
| `sounddevice` | 0.5.6 | MIT | Lazy PortAudio-backed capture |
| `PortAudio` bundled by `sounddevice` wheel | 0.5.6 wheel payload | MIT-style | Native audio I/O library |
| `certifi` | 2026.11.20 | MPL-2.0 | CA certificate bundle |
| `FastAPI` | 0.165.0 | MIT | Local HTTP/WebSocket sidecar |
| `Pydantic` | 2.14.4 | MIT | Configuration models |
| `python-dotenv` | 1.2.4 | BSD-3-Clause | Local `.env` loading |
| `uvicorn` | 0.54.0 | BSD-3-Clause | Local ASGI server |
| `websocket-client` | 1.9.2 | Apache-2.0 | Upstream WebSocket client |
| `websockets` | 17.4 | BSD-3-Clause | Uvicorn ASGI WebSocket protocol |
| `React` / `React DOM` | 19.2.8 | MIT | React settings and monitor UI |
| `Zustand` | 5.0.15 | MIT | Frontend settings state |
| `Tailwind CSS` | 3.4.19 | MIT | Frontend build styling |
| `Vite` | 8.3.8 | MIT | Locked frontend build tool |
| `lucide-react` | 1.31.0 | ISC | UI icons |
| `PyInstaller` | 6.16.0 | GPL-2.0-or-later with bootloader exception | Build-only one-file sidecar packaging; no GPL runtime SDK is linked into application source |
| `pyinstaller-hooks-contrib` | 2026.10 | GPL-2.0-or-later with exception | Build-only PyInstaller hooks |
| `@tauri-apps/cli` | 2.9.4 | MIT OR Apache-2.0 | Build-only `.app`/`.dmg` packaging CLI fallback |
| `tauri` / `tauri-build` / `tauri-plugin-opener` | 2.x | MIT OR Apache-2.0 | Apple Silicon macOS native desktop shell |
| `tauri-plugin-single-instance` | 2.4.3 | MIT OR Apache-2.0 | Single native overlay host and secondary-launch activation |
| `tauri-nspanel` | 2.1.1 | MIT OR Apache-2.0 | Persistent non-activating macOS `NSPanel` for the primary subtitle overlay |
| `wry` | 0.55.x | MIT OR Apache-2.0 | macOS WKWebView integration |
| `objc2` / `objc2-app-kit` / `objc2-foundation` | 0.6.x / 0.3.x | MIT | macOS AppKit / Foundation FFI bindings |

## Reused Source References

| Project | Fixed revision | License | Use boundary |
| :--- | :--- | :--- | :--- |
| `yuxino/Mimi` | `bce7164fcea4591b912050c298c9b72bdbeb6f61` (`v1.7.6`) | MIT | React settings-shell structure, five-section information architecture, visual hierarchy, package lock baseline, language-catalog shape, R10 Mac single-window Tauri v2 overlay (`overlay.rs`, `native.rs`, `macos_space.rs`, `macos_pointer.rs`, `macos_drag.rs`, `resize.rs`, `work_area.rs`, `ResizeHandles.tsx`, `OverlayDragArea.tsx`, `overlayChrome.css`), R12 WebSocket health/recovery patterns (`live_translate_client.rs`, `health.rs`, `recovery.rs`), and R15.5S real `NSPanel`, full-screen Space reassertion, and attached-backend lifecycle references |
| `SakiRinn/LiveCaptions-Translator` | `a6fee12757b15edbeef7c60f8895e0be694801e1` | Apache-2.0 | `ResizeForOnlyMode` single-window height compensation and bottom-anchor preservation reference (`src/windows/OverlayWindow.xaml.cs`) |
| `phuc-nt/my-translator` | `3495f99e3c524f43a64fd93307a0140c1a2b25c8` | MIT | Session-map, independent-engine lifecycle, and `ExitRequested` graceful shutdown ideas only |
| `HiMeditator/auto-caption` | `927a393561bf5fd33a6f3a1803a659558cf93b4d` | MIT | Sidecar lifecycle, `.venv` vs packaged binary launch resolution, readiness handshake, and process-tree cleanup patterns |
| `Samoyed123-yed/echo-translate` | `fc8d9a2195903cef780c8a295183b21a3561db12` | MIT | R13 synchronous filler, repeat, fragment, false-period and paragraph-boundary cleanup rules adapted from `public/app.js`; R14 silent correction cadence, untranslated-word detection and plausible-correction limits adapted from `public/app.js` and `server.py`; R15 exact narration-prefix cleanup and 1-5 importance semantics adapted from `server.py` |

`hayamimi` source is not copied because its license metadata was not
sufficiently clear for this project. No AGPL-3.0 source is copied.

## sounddevice

Copyright (c) Matthias Geier and contributors.

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## PortAudio

The `sounddevice` macOS wheel includes a PortAudio binary. PortAudio is
distributed under a permissive MIT-style license. The current packaged sidecar
is built from the locked `sounddevice` wheel and remains subject to the
sounddevice/PyInstaller dependency notices above. No separate proprietary
PortAudio runtime is added.

## yuxino/Mimi

Copyright (c) 2026 yuxino

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## phuc-nt/my-translator

Copyright (c) 2026 Personal Translator Contributors

Licensed under the MIT License text reproduced under `yuxino/Mimi` above.

## HiMeditator/auto-caption

Copyright (c) 2025 Chen Janai

Licensed under the MIT License text reproduced under `yuxino/Mimi` above.

## Samoyed123-yed/echo-translate

Copyright (c) 2026 Echo Translate contributors

Licensed under the MIT License text reproduced under `yuxino/Mimi` above.

## tauri-plugin-single-instance

Copyright 2019-2026 The Tauri Programme within The Commons Conservancy.

Licensed under either the MIT License or the Apache License, Version 2.0.
The MIT license text is reproduced under `yuxino/Mimi` above.

## tauri-nspanel

Copyright (c) 2026 Victor Aremu and tauri-nspanel contributors.

Licensed under either the MIT License or the Apache License, Version 2.0.
The MIT license text is reproduced under `yuxino/Mimi` above. The Apache
License, Version 2.0 text is reproduced under
`SakiRinn/LiveCaptions-Translator` below.

## SakiRinn/LiveCaptions-Translator

Copyright (c) SakiRinn and LiveCaptions-Translator contributors

                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS
