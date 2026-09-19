import { readFile, writeFile } from "node:fs/promises";
import { build } from "esbuild";

const outfile = "src/xiaomi_health_sync/static/oauth-consent.js";

await build({
  entryPoints: ["web/oauth-consent.js"],
  outfile,
  bundle: true,
  format: "esm",
  platform: "browser",
  target: "es2022",
  minify: true,
  legalComments: "eof",
});

const bundled = await readFile(outfile, "utf8");
await writeFile(outfile, bundled.replace(/[ \t]+$/gm, ""), "utf8");
