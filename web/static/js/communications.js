/* Christmas communications: text-token drag/drop and confidential previews. */
document.addEventListener("DOMContentLoaded", () => {
  const editor = document.getElementById("comm-editor");
  if (!editor) return;
  const subject = document.getElementById("comm-subject");
  const body = document.getElementById("comm-body");
  const targets = [subject, body];
  let lastFocused = body;

  function insertToken(el, token) {
    if (!el || !token) return;
    const start = Number.isInteger(el.selectionStart) ? el.selectionStart : el.value.length;
    const end = Number.isInteger(el.selectionEnd) ? el.selectionEnd : start;
    el.value = el.value.slice(0, start) + token + el.value.slice(end);
    el.focus();
    el.setSelectionRange(start + token.length, start + token.length);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  targets.forEach(el => {
    el.addEventListener("focus", () => { lastFocused = el; });
    el.addEventListener("dragover", e => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
    el.addEventListener("drop", e => {
      e.preventDefault();
      const token = e.dataTransfer.getData("text/plain");
      insertToken(el, token);
    });
  });
  document.querySelectorAll("[data-merge-token]").forEach(chip => {
    chip.addEventListener("dragstart", e => {
      e.dataTransfer.effectAllowed = "copy";
      e.dataTransfer.setData("text/plain", chip.dataset.mergeToken);
    });
    chip.addEventListener("click", () => insertToken(lastFocused, chip.dataset.mergeToken));
  });
  const switcher = document.getElementById("comm-switch");
  switcher?.addEventListener("change", () => { window.location.href = switcher.value; });

  const previewButton = document.getElementById("comm-preview-button");
  const previewError = document.getElementById("comm-preview-error");
  const previewCard = document.getElementById("comm-preview-card");
  const csrf = editor.querySelector("input[name='csrf_token']").value;

  async function preview() {
    previewError.textContent = "";
    previewCard.hidden = true;
    previewButton.disabled = true;
    const data = new FormData();
    data.append("csrf_token", csrf);
    data.append("preview_family_id", document.getElementById("comm-preview-family").value);
    data.append("subject", subject.value);
    data.append("body", body.value);
    try {
      const response = await fetch("/christmas/admin/communications/preview", {
        method: "POST", credentials: "same-origin", body: data,
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "Unable to preview");
      document.getElementById("comm-preview-to").textContent =
        result.family + (result.to ? " <" + result.to + ">" : " (no email address on file)");
      document.getElementById("comm-preview-subject").textContent = result.subject;
      document.getElementById("comm-preview-body").textContent = result.body;
      previewCard.hidden = false;
    } catch (e) {
      previewError.textContent = String(e.message || "Preview unavailable.");
    } finally {
      previewButton.disabled = false;
    }
  }
  previewButton?.addEventListener("click", preview);

  const sendForm = document.getElementById("comm-send-form");
  const singleFamily = document.getElementById("comm-send-one");
  const radios = sendForm?.querySelectorAll("input[name='scope']");
  function toggleScope() {
    const all = sendForm.querySelector("input[name='scope']:checked")?.value === "all";
    singleFamily.hidden = !!all;
  }
  radios?.forEach(radio => radio.addEventListener("change", toggleScope));
  if (sendForm) toggleScope();
});
