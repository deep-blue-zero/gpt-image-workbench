Windows x64 distribution with Python and dependencies included.

- **Installer:** download the file ending in `-setup.exe`, install, and open GPT Image Workbench from Start.
- **Portable:** extract the complete `-portable.zip` into a writable folder and open `GPTImageWorkbench.exe`.
- Paste your own API key into **Connect OpenAI / API-key settings**. Remember saves it in Windows Credential Manager; session-only use is also available.
- Explore without an API key. Saving a key never submits an image request. Paid generation requires a cost preview and confirmation.
- The launcher reopens the browser, selects an existing library, and waits for active requests to save when quitting.
- Installer libraries live separately under `%LOCALAPPDATA%\GPT Image Workbench\data`; portable libraries default to the extracted `data` folder. Upgrades and uninstall preserve libraries.

The initial Windows packages are **unsigned**. Windows may show an unknown-publisher or SmartScreen warning. SHA-256 checksums are in `SHA256SUMS.txt`; download only from this repository's Releases page.

These are Windows x64 builds. Other operating systems and native ARM64 packages are not qualified. Packaging checks are offline and do not submit paid API requests.
