/**
 * Family Christmas Gift Exchange - Interactive UI Utilities
 * Vanilla JS, no external libraries.
 */

document.addEventListener("DOMContentLoaded", () => {
  // 1. Copy to clipboard handlers
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async (e) => {
      e.preventDefault();
      const targetSelector = button.getAttribute("data-copy-target");
      const targetElem = document.querySelector(targetSelector);
      const textToCopy = targetElem ? (targetElem.value || targetElem.textContent) : button.getAttribute("data-copy-text");

      if (!textToCopy) return;

      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(textToCopy);
        } else {
          // Fallback for non-https or older contexts
          const textarea = document.createElement("textarea");
          textarea.value = textToCopy;
          textarea.style.position = "fixed";
          textarea.style.opacity = "0";
          document.body.appendChild(textarea);
          textarea.focus();
          textarea.select();
          document.execCommand("copy");
          document.body.removeChild(textarea);
        }

        const originalText = button.textContent;
        button.textContent = "Copied! ✓";
        button.classList.add("btn-forest");

        setTimeout(() => {
          button.textContent = originalText;
          button.classList.remove("btn-forest");
        }, 2000);
      } catch (err) {
        console.error("Failed to copy:", err);
      }
    });
  });

  // 2. Destructive confirmation prompts
  document.querySelectorAll("[data-confirm]").forEach((element) => {
    element.addEventListener("click", (e) => {
      const message = element.getAttribute("data-confirm");
      if (!confirm(message || "Are you sure you wish to proceed?")) {
        e.preventDefault();
        e.stopPropagation();
      }
    });
  });

  // 3. Flash alert dismissal
  document.querySelectorAll(".alert-close").forEach((closeBtn) => {
    closeBtn.addEventListener("click", () => {
      const alert = closeBtn.closest(".alert");
      if (alert) {
        alert.style.opacity = "0";
        setTimeout(() => alert.remove(), 200);
      }
    });
  });
});
