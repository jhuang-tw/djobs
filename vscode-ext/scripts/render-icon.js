// One-off: rasterize media/logo.svg to a 128x128 PNG for the marketplace icon.
const fs = require("fs");
const path = require("path");
const { Resvg } = require("@resvg/resvg-js");

const mediaDir = path.join(__dirname, "..", "media");
const svg = fs.readFileSync(path.join(mediaDir, "logo.svg"), "utf8");
const resvg = new Resvg(svg, {
  fitTo: { mode: "width", value: 128 },
  background: "rgba(0,0,0,0)",
});
const png = resvg.render().asPng();
fs.writeFileSync(path.join(mediaDir, "icon-128.png"), png);
console.log("wrote media/icon-128.png", png.length, "bytes");
