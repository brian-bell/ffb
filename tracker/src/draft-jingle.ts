/** A single audio element keeps repeated clicks from layering multiple clips. */
export function wireDraftJingle(): void {
  const button = document.querySelector<HTMLButtonElement>("[data-draft-jingle]");
  const audio = document.querySelector<HTMLAudioElement>("[data-draft-jingle-audio]");
  const status = document.querySelector<HTMLElement>("[data-jingle-status]");
  if (!button || !audio || !status) return;

  let attempt = 0;
  button.addEventListener("click", async () => {
    const currentAttempt = ++attempt;
    status.textContent = "";
    try {
      audio.currentTime = 0;
      await audio.play();
    } catch {
      if (currentAttempt === attempt) {
        status.textContent = "Couldn’t play the jingle. Try again.";
      }
    }
  });
}
