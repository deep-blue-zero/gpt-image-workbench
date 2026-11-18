GPT Image Workbench 0.2.1 fixes WebP reference uploads in **Edit/Image to Image**.
On some Windows systems, version 0.2.0 uploaded a still WebP as
`application/octet-stream`, and OpenAI rejected it with `unsupported_file_mimetype`.
Standard edits and Batch reference uploads now send the correct image type.
You can reuse WebP images already in your library in their original format.

- For an installed copy, quit Workbench and run the new `-setup.exe`. Open GPT Image Workbench from Start to use your existing library.
- For a portable copy, extract the complete `-portable.zip` to a fresh folder, open `GPTImageWorkbench.exe`, and use **Choose library** to select your existing `data` folder.

Both Windows x64 downloads include Python and are **unsigned**. Windows may show a SmartScreen or unknown-publisher warning. SHA-256 checksums are in `SHA256SUMS.txt`.
