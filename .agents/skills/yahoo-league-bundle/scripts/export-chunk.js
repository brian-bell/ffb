// Return one 1000-character slice of window.__ffbCapture (set by capture.js).
// Browser tool output truncates long results, so export chunk 0..N-1 and
// concatenate them verbatim, in order, into capture.json.
const CHUNK = 0;
JSON.stringify(window.__ffbCapture).slice(CHUNK * 1000, (CHUNK + 1) * 1000);
