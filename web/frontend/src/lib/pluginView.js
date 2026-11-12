// A plugin's result screen (register_view) is a document the plugin made, so the workbench keeps it apart from itself.
// The frame that shows it is <iframe sandbox="allow-scripts"> without allow-same-origin: its scripts run (tooltips,
// folding), but in an origin of their own, with no reach into the workbench page, its storage, or readable answers
// from the API. This policy is the second half: the document may use only what it carries inline. Nothing is loaded
// from, and nothing is fetched or posted to, anywhere else, which is the contract of register_view (one complete
// document, no outside file).
const POLICY = [
  "default-src 'none'",
  "style-src 'unsafe-inline'",
  "script-src 'unsafe-inline'",
  'img-src data: blob:',
  'font-src data:',
  'media-src data: blob:',
  "form-action 'none'",
  "base-uri 'none'"
].join('; ');

// The document with the policy as its first <meta>, placed after <head> (or <html>, or the doctype) so that the
// doctype stays first and the page keeps its rendering mode.
export function isolatedDocument(html) {
  const text = String(html ?? '');
  const meta = `<meta http-equiv="Content-Security-Policy" content="${POLICY}">`;
  const opening = /<head(\s[^>]*)?>/i.exec(text) || /<html(\s[^>]*)?>/i.exec(text) || /<!doctype[^>]*>/i.exec(text);
  const at = opening ? opening.index + opening[0].length : 0;
  return text.slice(0, at) + meta + text.slice(at);
}
