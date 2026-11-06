// Copy check: fails if UI source, fixtures or docs contain internal working terms,
// priority labels, decision references, numbered working-doc file names, or any
// Arabic script (the UI is English only; local-language text is never embedded).
//
//   npm run check:copy
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const TARGETS = ["src", "e2e", "public", "index.html", "README.md"];
const EXTENSIONS = new Set([".ts", ".tsx", ".js", ".mjs", ".json", ".css", ".html", ".md", ".svg"]);

const RULES = [
  { name: "internal working term", re: /\bplanning\b/i },
  { name: "internal working term", re: /stress[\s_-]?test/i },
  { name: "internal working term", re: /hackathon/i },
  { name: "internal working term", re: /\bITU\b/i },
  { name: "internal working term", re: /\bmentors?\b/i },
  { name: "priority label", re: /\bP[0-3]\b/ },
  { name: "decision reference", re: /\bdecisions?\s+(no\.?\s*)?#?\d+\b/i },
  { name: "decision reference", re: /(^|[\s(])#\d{1,3}\b(?![0-9a-f])/ },
  { name: "working-doc file name", re: /\b\d{2}-[a-z][a-z0-9-]+\.md\b/ },
  { name: "Arabic script", re: /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/ },
];

// Real public document titles from the manifest that happen to contain a listed word.
const ALLOWED = [/Pharmacovigilance Planning/g, /pharmacovigilance-planning/g, /Planning and Design/g, /planning-and-design/g];

function walk(path, out) {
  const st = statSync(path, { throwIfNoEntry: false });
  if (!st) return;
  if (st.isDirectory()) {
    for (const name of readdirSync(path)) if (name !== "node_modules" && name !== "dist") walk(join(path, name), out);
  } else if (EXTENSIONS.has(extname(path))) out.push(path);
}

const files = [];
for (const t of TARGETS) walk(join(root, t), files);

const problems = [];
for (const file of files) {
  const lines = readFileSync(file, "utf8").split("\n");
  lines.forEach((raw, i) => {
    let line = raw;
    for (const a of ALLOWED) line = line.replace(a, "");
    for (const rule of RULES) {
      const m = line.match(rule.re);
      if (m) problems.push(`${relative(root, file)}:${i + 1}: ${rule.name}: "${m[0].trim()}"`);
    }
  });
}

if (problems.length) {
  console.error(`check:copy found ${problems.length} problem(s):`);
  for (const p of problems.slice(0, 200)) console.error(`  ${p}`);
  process.exit(1);
}
console.log(`check:copy passed: ${files.length} files scanned.`);
