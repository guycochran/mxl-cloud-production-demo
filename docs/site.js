(() => {
  const descriptions = {
    live: "A seven-input selector, live layout compositor, program audio mixer and HTML5 keyer operate against flows in a single shared-memory domain.",
    fabric: "Finished program and guest contribution cross between hosts through the MXL Fabrics API. In testing, one 1080p30 v210 flow sustained 30 grains per second at approximately 1.3 Gbps.",
    archive: "The live program is segmented and registered into a TAMS store with capture-derived timeranges, enabling live scrub, instant timerange clips and zero-copy clip registration.",
    control: "An IS-04 node exposes domain flows as NMOS resources with BCP-007-03 tags and live PGM/PVW state; Bitfocus Companion provides a practical hardware-control bridge."
  };

  const tabs = document.querySelectorAll("[data-mode]");
  const diagram = document.querySelector("#architecture-diagram");
  const description = document.querySelector("#mode-description");

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const mode = tab.dataset.mode;
      tabs.forEach((item) => {
        const selected = item === tab;
        item.classList.toggle("active", selected);
        item.setAttribute("aria-selected", String(selected));
      });
      diagram.className = `architecture-grid mode-${mode}`;
      description.textContent = descriptions[mode];
    });
  });

  const copyButton = document.querySelector("[data-copy]");
  copyButton?.addEventListener("click", async () => {
    const original = copyButton.textContent;
    try {
      await navigator.clipboard.writeText(copyButton.dataset.copy);
      copyButton.textContent = "COPIED ✓";
    } catch {
      copyButton.textContent = "SELECT COMMAND ABOVE";
    }
    window.setTimeout(() => { copyButton.textContent = original; }, 1800);
  });
})();
