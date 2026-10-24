"""Synthetic BytePlus pricing page in the rich-text format the live page ships (no raw page is stored)."""

import json

from scripts.market_collection.models import BYTEPLUS_ID

FORMULA = "(Input video duration + output video duration) × output video width × output video height × output video frame rate / 1024"
WITHOUT_VIDEO = [
    ["Model", "Online inference", "", "", "Offline inference"],
    ["", "480p and 720p", "1080p", "4K", ""],
    [BYTEPLUS_ID, "$10.70", "$11.7", "Not supported", "Not supported"],
    ["dreamina-seedance-2-0-260128", "$7.0", "$7.7", "$4.0", "Not supported"],
]
WITH_VIDEO = [*WITHOUT_VIDEO[:2], [BYTEPLUS_ID, "$6.40", "$7.0", "Not supported", "Not supported"]]


def _zone(key, *ops):
    return {"zoneId": key, "zoneType": "Z", "ops": list(ops)}


def _line(*inserts):
    return [{"insert": "*", "attributes": {"lineId": "line"}}, *inserts]


def byteplus_page(tabs):
    """The pricing page as BytePlus ships it: the document is rich-text zones inside the router state.

    A tab zone hosts each table, the table names its row and column id zones, and a cell is the zone x<row>x<col>.
    """
    root = [*_line({"insert": "Token prices", "attributes": {"bold": "true"}}, {"insert": " are USD per million tokens.\n"}),
            *_line({"insert": "Estimated token consumption:", "attributes": {"bold": "true"}}, {"insert": " "},
                   {"insert": FORMULA, "attributes": {"inlineCode": "true"}}, {"insert": "\n"}),
            *_line({"insert": "Token consumption values are estimates. Actual consumption is determined by "},
                   {"insert": "usage.completion_tokens", "attributes": {"inlineCode": "true"}}, {"insert": " in the API response.\n"}),
            *_line({"insert": "Seedance 2.0 mini: 60% off the list price.\n"})]
    zones, tab_ops = {"0": _zone("0", *root)}, []
    for number, (title, grid) in enumerate(tabs):
        host, rows, cols = f"t{number}", [f"t{number}r{n}" for n in range(len(grid))], [f"t{number}c{n}" for n in range(len(grid[0]))]
        tab_ops.append({"insert": " ", "attributes": {"tabContent": "true", "title": title, "zoneId": host}})
        zones[host] = _zone(host, {"insert": "*", "attributes": {"aceTable": f"{host}rows {host}cols"}}, {"insert": "\n"})
        zones[f"{host}rows"] = _zone(f"{host}rows", *({"insert": {"id": row}} for row in rows))
        zones[f"{host}cols"] = _zone(f"{host}cols", *({"insert": {"id": col}} for col in cols))
        for row, cells in zip(rows, grid):
            for col, text in zip(cols, cells):
                zones[f"x{row}x{col}"] = _zone(f"x{row}x{col}", *_line({"insert": text + "\n"}))
    zones["tabs"] = _zone("tabs", *tab_ops, {"insert": "\n"})
    document = json.dumps({"version": "0.4.37", "data": zones})
    state = {"loaderData": {"(lang)/docs/(libcode)/(doccode$)/page": {"curDoc": {"Title": "Pricing", "Content": document}}}}
    return f"<html><body><script>window._ROUTER_DATA = {json.dumps(state)}</script></body></html>".encode()


BYTEPLUS = byteplus_page([("Input without video", WITHOUT_VIDEO), ("Input with video", WITH_VIDEO)])
