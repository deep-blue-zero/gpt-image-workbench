() => {
    document.documentElement.classList.add("dark");
    document.body.classList.add("dark");
    if (document.documentElement.dataset.promptImageDrops === "ready") return;
    document.documentElement.dataset.promptImageDrops = "ready";

    let restarting = false;
    document.addEventListener("click", async (event) => {
        const button = event.target instanceof Element && event.target.closest("#restart-workbench");
        if (!button || restarting) return;
        const status = document.querySelector("[data-restart-instance]");
        if (!status || !window.confirm("Restart Workbench? Unsaved form entries and session-only API keys will be cleared.")) return;
        const instance = status.dataset.restartInstance;
        restarting = true;
        button.disabled = true;
        status.textContent = "Requesting restart…";
        try {
            const response = await fetch("/workbench/restart", {
                method: "POST",
                headers: { "X-Workbench-Instance": instance },
                signal: AbortSignal.timeout(5000),
            });
            if (!response.ok) {
                const details = await response.json().catch(() => ({}));
                status.textContent = details.detail || "Workbench could not restart. Try again.";
                button.disabled = false;
                restarting = false;
                return;
            }
        } catch {
            // The server can disconnect just after accepting the request. Check before retrying.
        }
        status.textContent = "Restarting Workbench… This tab will reconnect automatically.";
        const deadline = Date.now() + 120000;
        while (Date.now() < deadline) {
            await new Promise(resolve => setTimeout(resolve, 800));
            try {
                const response = await fetch("/workbench/instance", {
                    cache: "no-store", signal: AbortSignal.timeout(2000),
                });
                if (response.ok && (await response.json()).restarted_from === instance) {
                    window.location.reload();
                    return;
                }
            } catch { /* Expected while the server is restarting. */ }
        }
        status.textContent = "Couldn’t reconnect. Check the launcher or terminal, then reload this tab.";
        button.disabled = false;
        restarting = false;
    });

    const promptTarget = (event) => event.target instanceof Element
        ? event.target.closest(".prompt-image-drop") : null;
    const referenceTarget = (event) => event.target instanceof Element
        ? event.target.closest(".reference-image-drop") : null;
    const hasFiles = (event) => Array.from(event.dataTransfer?.types || []).includes("Files");
    const stop = (event) => { event.preventDefault(); event.stopPropagation(); };
    const referenceDrops = new WeakMap();
    // Gradio removes the native file input while uploading, then renders a new one.
    // Waiting for that replacement serializes drops even when the next arrives mid-upload.
    const referenceInput = (target, previous = null) => new Promise((resolve, reject) => {
        const ready = () => {
            const input = target.querySelector('input[type="file"]');
            return input && input !== previous ? input : null;
        };
        const present = ready();
        if (present) { resolve(present); return; }
        const observer = new MutationObserver(() => {
            const input = ready();
            if (input) { observer.disconnect(); clearTimeout(timeout); resolve(input); }
        });
        const timeout = setTimeout(() => {
            observer.disconnect();
            reject(new Error("The reference upload did not finish. Check the file list and upload message before trying again."));
        }, 120000);
        observer.observe(target, { childList: true, subtree: true });
    });
    const appendReferenceDrop = (target, files) => {
        // Retain the Files synchronously, before the drop's protected data is cleared.
        const retained = new DataTransfer();
        for (const file of files) retained.items.add(file);
        const previous = referenceDrops.get(target) || Promise.resolve();
        const next = previous.then(async () => {
            const input = await referenceInput(target);
            const finished = referenceInput(target, input);
            input.value = "";
            input.files = retained.files;
            // The existing Gradio upload handler appends to its current ordered list.
            input.dispatchEvent(new Event("change", { bubbles: true }));
            await finished;
        }).catch((error) => window.alert(error.message));
        referenceDrops.set(target, next);
    };
    const waitForElement = (selector) => new Promise((resolve) => {
        const present = document.querySelector(selector);
        if (present) { resolve(present); return; }
        const observer = new MutationObserver(() => {
            const element = document.querySelector(selector);
            if (element) { observer.disconnect(); clearTimeout(timeout); resolve(element); }
        });
        const timeout = setTimeout(() => { observer.disconnect(); resolve(null); }, 2000);
        observer.observe(document.body, { childList: true, subtree: true });
    });

    document.addEventListener("dragover", (event) => {
        const target = referenceTarget(event) || promptTarget(event);
        if (!target || !hasFiles(event)) return;
        stop(event);
        event.dataTransfer.dropEffect = "copy";
        target.classList.add("image-dragover");
    }, true);

    document.addEventListener("dragleave", (event) => {
        const target = referenceTarget(event) || promptTarget(event);
        if (target && !target.contains(event.relatedTarget)) target.classList.remove("image-dragover");
    }, true);

    document.addEventListener("drop", async (event) => {
        const references = referenceTarget(event);
        if (references && hasFiles(event)) {
            stop(event);
            references.classList.remove("image-dragover");
            const files = Array.from(event.dataTransfer.files);
            if (!files.length || files.some(file => !/\.(png|jpe?g|webp)$/i.test(file.name))) {
                window.alert("Drop PNG, JPEG, or WebP reference images.");
                return;
            }
            appendReferenceDrop(references, files);
            return;
        }
        const prompt = promptTarget(event);
        if (!prompt || !hasFiles(event)) return;
        stop(event);
        prompt.classList.remove("image-dragover");
        const files = event.dataTransfer.files;
        if (files.length !== 1 || !/\.(png|jpe?g|webp)$/i.test(files[0].name)) {
            window.alert("Drop one PNG, JPEG, or WebP image at a time.");
            return;
        }
        // Preserve the File before the native drop event's protected data is cleared.
        const retained = new DataTransfer();
        retained.items.add(files[0]);
        const prefix = prompt.id === "edit-prompt" ? "edit" : "generate";
        const panel = document.getElementById(`${prefix}-metadata-panel`);
        const toggle = panel?.querySelector(":scope > button");
        if (toggle && !toggle.classList.contains("open")) toggle.click();
        const upload = await waitForElement(`#${prefix}-metadata-file`);
        upload?.querySelector('button[aria-label="Clear"]')?.click();
        const input = await waitForElement(`#${prefix}-metadata-file input[type="file"]`);
        if (!input) {
            window.alert("Open Recover prompt & settings from an image and choose the file there.");
            return;
        }
        input.value = "";
        input.files = retained.files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
    }, true);
}
