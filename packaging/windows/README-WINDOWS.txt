GPT IMAGE WORKBENCH

Open GPTImageWorkbench.exe. The launcher opens the workbench in your browser.
Keep the launcher running while you use the workbench. Use Quit Workbench to stop.

First use: open Connect OpenAI / API-key settings, paste your own OpenAI API key,
and choose Save API key. Remember stores it in Windows Credential Manager for
this library. Uncheck Remember to use the key only until you quit. Saving a key
does not send an image request. API usage is billed separately by OpenAI.

You can explore without a key. Paid requests require a cost preview and confirmation.

Installer: libraries default to %LOCALAPPDATA%\GPT Image Workbench\data.
Portable ZIP: extract the entire ZIP into a writable folder, then open the EXE.
Keep portable.flag and _internal together with the EXE. Its library is in data.
Portable keys remain in Windows Credential Manager on this computer; they do
not travel inside the ZIP or library. Reconnect your key after moving a library.

Choose library in the launcher to open an existing data folder containing
workbench.sqlite3. Stop other Workbench instances using that library first.
Back up the library while the app is stopped. Images can use substantial space.

Updates: the installer preserves your library and settings. For portable updates,
quit, extract the new ZIP to a fresh folder, and choose the existing library.
Uninstalling the program preserves user libraries and saved credentials.
Disconnect and forget the key in the interface before uninstalling if desired.

This initial distribution is unsigned. Windows may identify it as an unknown
publisher. Download only from the project's Releases page and review SHA256SUMS.

Downloads: https://github.com/deep-blue-zero/gpt-image-workbench/releases/latest
Help: https://github.com/deep-blue-zero/gpt-image-workbench/issues

MIT licensed. Third-party software retains its respective licenses; see
_internal package metadata and Python-LICENSE.txt distributed with the program.
