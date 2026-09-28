// Return part CHUNK of the capture as an array of up to 20 strings of at most
// 900 characters each (window.__ffbExport, split by capture.js without breaking
// surrogate pairs). The browser JavaScript tool cuts any single string longer
// than 1000 characters but returns an array of shorter strings whole, as JSON
// (a 21,000-character array came back intact on 2026-09-27). One part covers
// about 18,000 characters, so a week's capture (about 7,300) is one call.
// Paste each returned array verbatim, in order, into the parts file.
// The block scope lets the script run again in the same tab.
{
  const CHUNK = 0;
  window.__ffbExport.slice(CHUNK * 20, (CHUNK + 1) * 20);
}
