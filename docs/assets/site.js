"use strict";

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const source = document.getElementById(button.dataset.copyTarget);
    const status = button.closest(".install-card").querySelector(".copy-status");
    try {
      await navigator.clipboard.writeText(source.textContent.trim());
      status.textContent = "Commands copied. Follow the README for model setup before scanning.";
      button.textContent = "Copied ✓";
    } catch {
      status.textContent = "Select and copy the commands above to use them in your terminal.";
    }
  });
});
