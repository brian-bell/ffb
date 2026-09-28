// Return part CHUNK of window.__ffbCapture (set by capture.js) as an array of
// 900-character strings. The browser JavaScript tool cuts any single string
// longer than 1000 characters but returns an array of shorter strings whole,
// as JSON (a 21,000-character array came back intact on 2026-09-27). One part
// covers 18,000 characters, so a week's capture (about 7,300) is one call.
// Paste each returned array verbatim, in order, into the parts file.
// The block scope lets the script run again in the same tab.
{
  const CHUNK = 0;
  const PIECE = 900;
  const PIECES = 20;
  const text = JSON.stringify(window.__ffbCapture);
  const start = CHUNK * PIECE * PIECES;
  Array.from({ length: PIECES }, (_, i) =>
    text.slice(start + i * PIECE, start + (i + 1) * PIECE),
  ).filter(Boolean);
}
