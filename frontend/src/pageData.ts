/** The JSON Django embeds in the shell as <script id="page-data">, or null. */
export function readPageData(doc: Document = document): unknown {
  const text = doc.getElementById("page-data")?.textContent;
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}
