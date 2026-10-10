"""Exercise the shipped edit handler with HTMLFormControlsCollection's item method."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


def test_edit_prefills_product_and_saves_without_losing_order_fields():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the frontend handler regression")
    page = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text()
    source = re.search(r'<script type="module">(.*?)</script>', page, re.S).group(1)
    order = dict(id=1, item="Acceptance product", merchant="Example shop",
                 order_no="TEST-1", delivery_date="2026-10-08",
                 window_start="18:00", window_end="20:00", note="Keep this note",
                 status="ordered", reminders=[])
    harness = r"""
const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const fields = Object.fromEntries(Object.keys(data.order).map(k => [k, {value: ''}]));
const elements = Object.assign({}, fields, {
  item: function item() {},
  namedItem: name => fields[name]
});
const errors = [], requests = [];
const nodes = {
  '#view': {insertAdjacentHTML: (where, html) => errors.push(html)},
  '#add': {elements, scrollIntoView() {}},
  '#test': {}, '#form-h': {}
};
const document = {querySelector: s => nodes[s], querySelectorAll: () => []};
const fetch = async (url, opts) => {
  requests.push(opts?.method || 'GET');
  return {ok: true, json: async () => ({orders: [data.order]})};
};
function FormData() {return Object.entries(fields).map(([k, v]) => [k, v.value]);}
const run = new Function('document', 'window', 'location', 'fetch', 'fields', 'FormData', 'errors', 'requests',
  'return (async () => {' + data.source + `
    await orders();
    await document.querySelector('#view').onclick({target: {
      closest: () => ({dataset: {edit: '1'}})
    }});
    await document.querySelector('#add').onsubmit({
      preventDefault() {}, target: document.querySelector('#add')
    });
    await new Promise(setImmediate);
    return {values: Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, v.value])),
            errors, requests};
  })()`);
run(document, {addEventListener() {}}, {hash: '#orders'}, fetch, fields, FormData, errors, requests)
  .then(result => console.log(JSON.stringify(result)))
  .catch(error => {console.error(error); process.exitCode = 1;});
"""
    proc = subprocess.run([node, "-e", harness], input=json.dumps(dict(source=source, order=order)),
                          text=True, capture_output=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    values = result["values"]
    for key in ("id", "item", "merchant", "order_no", "delivery_date",
                "window_start", "window_end", "note"):
        assert values[key] == order[key], f"edit did not prefill {key}"
    assert result["errors"] == [], "successful save must refresh the order list without an error"
    assert result["requests"][-2:] == ["POST", "GET"]
