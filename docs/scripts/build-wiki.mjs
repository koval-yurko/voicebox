#!/usr/bin/env node
// Convert the Fumadocs MDX in content/docs to GitHub wiki Markdown in wiki-out/.
// Usage: node scripts/build-wiki.mjs [outDir]
// The script fails when a page name repeats, a component tag is left over,
// or an internal link points to a page that does not exist.

import fs from 'node:fs';
import path from 'node:path';

const DOCS = path.resolve(import.meta.dirname, '..');
const SRC = path.join(DOCS, 'content/docs');
const OUT = path.resolve(process.argv[2] ?? path.join(DOCS, 'wiki-out'));
const API_DOCS_URL = 'https://voice.kovalchuk.win/docs';
const LOCAL_API_DOCS_URL = 'http://127.0.0.1:17593/docs';
const CALLOUT = { info: 'NOTE', warn: 'WARNING', warning: 'WARNING', success: 'TIP', error: 'CAUTION' };
const COMPONENTS = /<\/?(Steps|Step|Cards|Card|Callout|AccordionGroup|Accordion|Tabs|Tab|Files|Folder|File|APIPage)\b/;

const readJson = (p) => JSON.parse(fs.readFileSync(p, 'utf8'));
const attr = (tag, name) => tag.match(new RegExp(`${name}="([^"]*)"`))?.[1];

function frontmatter(text) {
  const m = text.match(/^---\n([\s\S]*?)\n---\n/);
  if (!m) return { meta: {}, body: text };
  const meta = {};
  for (const line of m[1].split('\n')) {
    const kv = line.match(/^(\w+):\s*"?(.*?)"?\s*$/);
    if (kv) meta[kv[1]] = kv[2];
  }
  return { meta, body: text.slice(m[0].length) };
}

// Wiki page names are flat and come from the file name, so use the page title
// (with the section title outside Overview, because some titles repeat).
const pageName = (title) => title.replace(/[^A-Za-z0-9 -]/g, '').trim().replace(/\s+/g, '-');

// Collect pages in sidebar order from meta.json. The API reference section becomes one page.
const pages = []; // { route, file, title, name, description }
const sidebar = [];
for (const section of readJson(path.join(SRC, 'meta.json')).pages) {
  const meta = readJson(path.join(SRC, section, 'meta.json'));
  sidebar.push(`### ${meta.title}`, '');
  if (section === 'api-reference') {
    sidebar.push('- [[API Reference|API-Reference]]', '');
    continue;
  }
  for (const slug of meta.pages) {
    const file = path.join(SRC, section, `${slug}.mdx`);
    const { meta: fm } = frontmatter(fs.readFileSync(file, 'utf8'));
    const page = { route: `/${section}/${slug}`, file, title: fm.title, name: pageName(section === 'overview' ? fm.title : `${meta.title} ${fm.title}`), description: fm.description };
    pages.push(page);
    sidebar.push(`- [[${page.title}|${page.name}]]`);
  }
  sidebar.push('');
}
const names = pages.map((p) => p.name).concat('Home', 'API-Reference');
const dup = names.find((n, i) => names.indexOf(n) !== i);
if (dup) throw new Error(`Two wiki pages share the name ${dup}`);
const byRoute = new Map(pages.map((p) => [p.route, p.name]));
byRoute.set('/', 'Home');
byRoute.set('/api-reference', 'API-Reference');

function rewriteLink(href) {
  if (href.startsWith('/images/')) return href.slice(1);
  const [route, hash] = href.split('#');
  if (!route.startsWith('/')) return href;
  const name = byRoute.get(route.replace(/\/$/, '') || '/') ?? (route.startsWith('/api-reference') && 'API-Reference');
  if (!name) throw new Error(`Unknown internal link ${href}`);
  return hash ? `${name}#${hash}` : name;
}

function convert(body, source) {
  const out = [];
  const stack = []; // open components, used for dedent and Callout quoting
  let fence = null;
  let tree = null; // lines of a <Files> block
  let card = null; // open multi-line <Card>

  const emit = (line) => {
    const quoted = stack.includes('Callout');
    out.push(quoted ? (line ? `> ${line}` : '>') : line);
  };

  for (const raw of body.split('\n')) {
    const depth = stack.filter((c) => c !== 'Files' && c !== 'Folder').length;
    const line = raw.replace(new RegExp(`^ {0,${depth * 2}}`), '');
    const t = line.trim();

    if (fence) {
      emit(line);
      if (t.startsWith(fence)) fence = null;
      continue;
    }
    if (/^(```|~~~)/.test(t)) {
      fence = t.slice(0, 3);
      emit(line);
      continue;
    }

    if (tree) {
      if (t === '</Files>') {
        out.push('```', ...tree, '```');
        tree = null;
        stack.pop();
      } else if (t.startsWith('<Folder')) {
        tree.push(`${'  '.repeat(stack.length - 1 - stack.indexOf('Files'))}${attr(t, 'name')}/`);
        if (!t.endsWith('/>')) stack.push('Folder');
      } else if (t === '</Folder>') {
        stack.pop();
      } else if (t.startsWith('<File')) {
        tree.push(`${'  '.repeat(stack.length - 1 - stack.indexOf('Files'))}${attr(t, 'name')}`);
      }
      continue;
    }

    if (card) {
      if (t === '</Card>') {
        const text = card.body.join(' ').trim();
        const title = card.href ? `[${card.title}](${rewriteLink(card.href)})` : card.title;
        emit(`- **${title}**${text ? ` — ${text}` : ''}`);
        card = null;
        stack.pop();
      } else card.body.push(t);
      continue;
    }

    if (/^\{\/\*.*\*\/\}$/.test(t)) continue;
    if (/^<\/?(Steps|Cards|AccordionGroup|Tabs)\b.*>$/.test(t)) {
      if (t.startsWith('</')) stack.pop();
      else if (!t.endsWith('/>')) stack.push(t.match(/^<(\w+)/)[1]);
      continue;
    }
    if (t.startsWith('<Callout')) {
      const kind = CALLOUT[attr(t, 'type') ?? 'info'] ?? 'NOTE';
      stack.push('Callout');
      emit(`[!${kind}]`);
      const title = attr(t, 'title');
      if (title) emit(`**${title}**`);
      continue;
    }
    if (t === '</Callout>') {
      stack.pop();
      out.push('');
      continue;
    }
    if (t.startsWith('<Step')) {
      stack.push('Step');
      emit(`### ${attr(t, 'title')}`);
      continue;
    }
    if (t.startsWith('<Tab ')) {
      stack.push('Tab');
      emit('');
      emit(`**${attr(t, 'value')}**`);
      emit('');
      continue;
    }
    if (t.startsWith('<Accordion ')) {
      stack.push('Accordion');
      emit(`<details><summary>${attr(t, 'title')}</summary>`);
      emit('');
      continue;
    }
    if (t === '</Accordion>') {
      emit('');
      emit('</details>');
      stack.pop();
      continue;
    }
    if (t === '</Step>' || t === '</Tab>') {
      stack.pop();
      continue;
    }
    if (t.startsWith('<Card')) {
      const c = { title: attr(t, 'title'), href: attr(t, 'href'), body: [] };
      if (t.endsWith('/>')) emit(`- **${c.href ? `[${c.title}](${rewriteLink(c.href)})` : c.title}**`);
      else {
        // Body text can follow the opening tag on the same line.
        const rest = t.replace(/^<Card(\s+\w+=("[^"]*"|\{(?:[^{}]|\{[^{}]*\})*\}))*\s*>/, '').trim();
        if (rest.endsWith('</Card>')) {
          const text = rest.replace('</Card>', '').trim();
          emit(`- **${c.href ? `[${c.title}](${rewriteLink(c.href)})` : c.title}**${text ? ` — ${text}` : ''}`);
        } else {
          if (rest) c.body.push(rest);
          card = c;
          stack.push('Card');
        }
      }
      continue;
    }
    if (t === '<Files>') {
      stack.push('Files');
      tree = [];
      continue;
    }

    const converted = line
      .replace(/\]\((\/[^)\s]*)\)/g, (_, href) => `](${rewriteLink(href)})`)
      .replace(/href="(\/[^"]*)"/g, (_, href) => `href="${rewriteLink(href)}"`);
    if (COMPONENTS.test(converted.replace(/`[^`]*`/g, ''))) throw new Error(`${source}: unconverted tag: ${t}`);
    emit(converted);
  }
  if (stack.length) throw new Error(`${source}: unclosed ${stack.join(', ')}`);
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
}

fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
fs.cpSync(path.join(DOCS, 'public/images'), path.join(OUT, 'images'), { recursive: true });

const header = (description) => (description ? `_${description}_\n\n` : '');
for (const p of pages) {
  const { body } = frontmatter(fs.readFileSync(p.file, 'utf8'));
  fs.writeFileSync(path.join(OUT, `${p.name}.md`), header(p.description) + convert(body, p.file));
}
const index = path.join(SRC, 'index.mdx');
const home = frontmatter(fs.readFileSync(index, 'utf8'));
fs.writeFileSync(path.join(OUT, 'Home.md'), `# ${home.meta.title}\n\n` + header(home.meta.description) + convert(home.body, index));

fs.writeFileSync(
  path.join(OUT, 'API-Reference.md'),
  `_The REST API of the Voicebox server._

The server publishes its own interactive reference (Swagger UI). It always matches the running server version.

- **Public:** ${API_DOCS_URL} — use the **Authorize** button to enter your API key.
- **Local:** ${LOCAL_API_DOCS_URL} — when the server runs on your machine.
- **Raw schema:** \`/openapi.json\` and ReDoc at \`/redoc\` on the same host.
`,
);
fs.writeFileSync(path.join(OUT, '_Sidebar.md'), ['**[[Home]]**', '', ...sidebar].join('\n'));
fs.writeFileSync(
  path.join(OUT, '_Footer.md'),
  '_This wiki is generated from [`docs/content`](https://github.com/koval-yurko/voicebox/tree/main/docs/content). Edit the files there, not here._\n',
);

// Self-check: every wiki link and image in the output must resolve.
const written = new Set(fs.readdirSync(OUT).filter((f) => f.endsWith('.md')).map((f) => f.slice(0, -3)));
for (const f of fs.readdirSync(OUT).filter((f) => f.endsWith('.md'))) {
  const text = fs.readFileSync(path.join(OUT, f), 'utf8').replace(/```[\s\S]*?```/g, '');
  const links = [...text.matchAll(/\]\(([^)\s:#]+)(#[^)]*)?\)/g), ...text.matchAll(/\[\[(?:[^\]|]*\|)?([^\]]+)\]\]/g)];
  for (const [, target] of links) {
    const ok = target.startsWith('images/') ? fs.existsSync(path.join(OUT, target)) : written.has(target);
    if (!ok) throw new Error(`${f}: broken link ${target}`);
  }
}
console.log(`Wrote ${written.size} pages to ${OUT}`);
