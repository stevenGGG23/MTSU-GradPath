// Reads an MTSU Degree Works audit PDF (the one Degree Works itself makes,
// "Ellucian Degree Works" in the file's properties) in the browser. The file
// never leaves the student's computer: pdf.js pulls out the text here, and
// parseAudit() turns it into completed / in-progress courses and the
// student's major, concentration and minor.
//
// The audit's course tables read, line by line:
//   [requirement]  CSCI 2170 | Computer Science II | A | 4 | Spring 2025
// with "(3)" credits and an "NA" grade for classes in progress, plus
// "Insufficient" (W, failed or superseded attempts) and "In-progress" tables.

const PDFJS_VERSION = '4.10.38';
const PDFJS_BASE = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${PDFJS_VERSION}/legacy/build/`;
export const MAX_AUDIT_BYTES = 15 * 1024 * 1024;

const CODE_RE = /^([A-Z]{2,4}) ?(\d{4}[A-Z]?)$/;
const TERM_RE = /^(Fall|Spring|Summer|Winter) (\d{4})$/;
const CREDITS_RE = /^\(?\d+(?:\.\d+)?\)?$/;
// Grades that earn the credit; transfer grades are T + letter (TA, TB+, TP)
const PASSING_RE = /^T?(?:[ABCD][+-]?|P|S|CR)$/;
const IN_PROGRESS_GRADES = new Set(['NA', 'IP', 'INP', '']);
const GRADE_RE = /^(?:T?(?:[ABCD][+-]?|F|P|S|U|CR|NC)|W|WF|WP|I|NA|IP|INP|AU|N|FA|FN|NR)$/;

// ── Text extraction (browser) ────────────────────────────────────────────────

let pdfjsPromise = null;

function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import(`${PDFJS_BASE}pdf.min.mjs`).then(pdfjs => {
      pdfjs.GlobalWorkerOptions.workerSrc = `${PDFJS_BASE}pdf.worker.min.mjs`;
      return pdfjs;
    }).catch(error => {
      pdfjsPromise = null;
      throw error;
    });
  }
  return pdfjsPromise;
}

/** {meta, lines}: the PDF's properties and its text as lines of items
 *  ({x, str}), pages in order, each page top to bottom. */
export async function readAuditFile(file) {
  const pdfjs = await loadPdfjs();
  const data = new Uint8Array(await file.arrayBuffer());
  const doc = await pdfjs.getDocument({ data, isEvalSupported: false }).promise;
  try {
    const { info } = await doc.getMetadata();
    const lines = [];
    for (let p = 1; p <= doc.numPages; p += 1) {
      const page = await doc.getPage(p);
      const content = await page.getTextContent();
      lines.push(...groupLines(content.items));
    }
    return { meta: info || {}, lines };
  } finally {
    doc.destroy();
  }
}

/** pdf.js text items -> lines (same baseline, give or take a point), each
 *  sorted left to right. */
export function groupLines(items) {
  const rows = [];
  items.forEach(item => {
    const str = (item.str || '').replace(/\s+/g, ' ').trim();
    if (!str) return;
    const y = item.transform[5];
    let row = rows.find(r => Math.abs(r.y - y) <= 1.5);
    if (!row) {
      row = { y, items: [] };
      rows.push(row);
    }
    row.items.push({ x: item.transform[4], str });
  });
  rows.sort((a, b) => b.y - a.y);
  return rows.map(row => row.items.sort((a, b) => a.x - b.x));
}

// ── Parsing ─────────────────────────────────────────────────────────────────

/** True only for the PDF Degree Works makes for an MTSU student. */
export function isMtsuDegreeWorks(meta, lines) {
  const creator = String(meta.Creator || '');
  const author = String(meta.Author || '');
  const text = lines.slice(0, 12).map(l => l.map(i => i.str).join(' ')).join(' ');
  return /degree ?works/i.test(creator)
    && (/middle tennessee state/i.test(author) || /middle tennessee state university/i.test(text));
}

function normCode(str) {
  const m = CODE_RE.exec(str.trim());
  return m ? `${m[1]} ${m[2]}` : null;
}

function sectionOf(line) {
  const first = line[0].str;
  if (/^insufficient$/i.test(first)) return 'insufficient';
  if (/^in-?progress$/i.test(first)) return 'in-progress';
  if (/^(fallthrough|over the limit)/i.test(first)) return 'extra';
  if (/^(not counted|exceptions|legend|disclaimer|split credits|planned)/i.test(first)) return 'ignore';
  // A requirement block's header: "Major - Computer Science, BS | IN-PROGRESS"
  if (line.some(i => /^(COMPLETE|IN-PROGRESS|INCOMPLETE|NOT COMPLETE)$/.test(i.str))) return 'block';
  return null;
}

/** Header values: "Major | Computer Science | Minor | Data Science | ..." */
function headerValues(lines, labels) {
  const out = {};
  labels.forEach(label => { out[label] = []; });
  lines.forEach(line => {
    line.forEach((item, index) => {
      const label = labels.find(l => item.str === l || item.str === `${l}:`);
      const next = line[index + 1];
      if (label && next && !labels.includes(next.str.replace(/:$/, ''))) {
        out[label].push(next.str);
      }
    });
  });
  return out;
}

/**
 * {ok, error?, completed: [{code, grade, term}], inProgress: [{code, term}],
 *  notCounted: [{code, grade}], majors, concentrations, minors, catalogYear,
 *  degree}
 */
export function parseAudit({ meta = {}, lines = [] }) {
  if (!isMtsuDegreeWorks(meta, lines)) {
    return { ok: false, error: 'not-degree-works' };
  }

  // The student header runs until the first requirement block
  const firstBlock = lines.findIndex(line => sectionOf(line) === 'block');
  const header = headerValues(lines.slice(0, firstBlock < 0 ? 15 : firstBlock),
    ['Major', 'Minor', 'Concentration', 'Degree', 'Program']);
  let catalogYear = '';
  lines.some(line => line.some(item => {
    const m = /^Catalog year:\s*(\d{4}-\d{4})/i.exec(item.str);
    if (m) catalogYear = m[1];
    return !!m;
  }));

  const passed = new Map();
  const inProgress = new Map();
  const notCounted = new Map();
  // Every graded attempt, for GPA hours (code|term -> attempt)
  const attempts = new Map();
  let section = 'block';
  // Which requirement each course was used for, block by block, the way
  // Degree Works applied it ("Math Elective: MATH 2010, MATH 2110")
  const blocks = [];
  const requirements = [];
  let block = null;
  let label = '';
  let context = [];
  let percent = null;

  lines.forEach(line => {
    // The name/ID line repeated at the top of every page
    if (/^middle tennessee state university$/i.test(line[0].str) && line.length > 1) return;
    if (percent === null && blocks.length === 0) {
      const pct = line.find(item => /^\d{1,3}%$/.test(item.str));
      if (pct) percent = Number(pct.str.replace('%', ''));
    }
    const next = sectionOf(line);
    if (next) {
      section = next;
      if (next === 'block') {
        const status = line.find(i => /^(COMPLETE|IN-PROGRESS|INCOMPLETE|NOT COMPLETE)$/.test(i.str)).str;
        block = { title: line[0].str, status, stillNeeded: false };
        blocks.push(block);
        label = '';
        context = [];
      }
      return;
    }
    if (section === 'ignore') return;
    if (block && line.some(item => /^still needed/i.test(item.str))) {
      block.stillNeeded = true;
    }
    const codeAt = line.findIndex(item => normCode(item.str));
    if (section === 'block' && codeAt < 0) {
      // A heading inside the block ("NATURAL SCIENCE OPTIONS") or a
      // wrapped label; the column headers and notes to the right are skipped
      const left = line.filter(item => item.x < 200).map(item => item.str).join(' ');
      if (left && !/^(course|credits required|your gpa|minor gpa)/i.test(left)) {
        context = [...context.slice(-2), left];
        label = '';
      }
      return;
    }
    if (section === 'block' && codeAt >= 0) {
      const rowLabel = line.slice(0, codeAt).map(item => item.str).join(' ');
      if (rowLabel) label = rowLabel;
    }
    // A course row: a code item, then title, grade, credits, term
    line.forEach((item, index) => {
      const code = normCode(item.str);
      if (!code) return;
      const rest = line.slice(index + 1, index + 7).map(i => i.str);
      const termIndex = rest.findIndex(s => TERM_RE.test(s));
      if (termIndex < 2) return;
      const credits = rest[termIndex - 1];
      if (!CREDITS_RE.test(credits)) return;
      const gradeText = rest[termIndex - 2];
      const grade = GRADE_RE.test(gradeText) ? gradeText : '';
      const term = rest[termIndex];
      if (section !== 'ignore') {
        attempts.set(`${code}|${term}`, { code, grade, term, credits: Number(credits.replace(/[()]/g, '')) || 0 });
      }
      const pending = credits.startsWith('(') || (IN_PROGRESS_GRADES.has(grade) && !PASSING_RE.test(grade));

      if (section === 'insufficient') {
        // W, failed, or an attempt a later grade replaced
        notCounted.set(code, { code, grade, term });
      } else if (section === 'in-progress' || pending) {
        inProgress.set(code, { code, term });
      } else if (PASSING_RE.test(grade)) {
        passed.set(code, { code, grade, term });
      } else if (grade) {
        notCounted.set(code, { code, grade, term });
      }
      if (section === 'block' && block && (pending || PASSING_RE.test(grade))) {
        const last = requirements[requirements.length - 1];
        if (last && last.block === block.title && last.label === label && last.context.join('|') === context.join('|')) {
          if (!last.codes.includes(code)) last.codes.push(code);
        } else {
          requirements.push({ block: block.title, label, context: context.slice(), codes: [code] });
        }
      }
    });
  });

  // A course passed on some attempt counts, whatever else is listed for it
  passed.forEach((_, code) => { inProgress.delete(code); notCounted.delete(code); });
  inProgress.forEach((_, code) => notCounted.delete(code));

  const byCode = (a, b) => a.code.localeCompare(b.code);
  const completed = [...passed.values()].sort(byCode);
  const pending = [...inProgress.values()].sort(byCode);
  if (!completed.length && !pending.length) {
    return { ok: false, error: 'no-courses' };
  }
  return {
    ok: true,
    completed,
    inProgress: pending,
    notCounted: [...notCounted.values()].sort(byCode),
    majors: header.Major,
    concentrations: header.Concentration,
    minors: header.Minor,
    degree: header.Degree[0] || '',
    catalogYear,
    percent,
    gpa: headerGpa(lines.slice(0, firstBlock < 0 ? 15 : firstBlock)),
    attempts: [...attempts.values()],
    blocks,
    requirements,
    stillNeeded: blocks.some(b => b.stillNeeded),
  };
}

/** What the plan needs from the audit (sent with the form; no name or ID):
 *  which course met which requirement, and each block's status. */
export function planPayload(result, majorKey, countInProgress) {
  return {
    major: majorKey || '',
    count_ip: !!countInProgress,
    percent: result.percent,
    catalog_year: result.catalogYear,
    blocks: result.blocks.map(b => ({ title: b.title, status: b.status, still_needed: b.stillNeeded })),
    rows: result.requirements.map(r => ({ block: r.block, label: r.label, context: r.context, codes: r.codes })),
  };
}

/** The term after *term* ("Fall 2026" -> "Spring 2027"), skipping summer
 *  unless asked. */
export function termAfter(term, includeSummer = false) {
  const m = TERM_RE.exec(term || '');
  if (!m) return null;
  let season = m[1];
  let year = Number(m[2]);
  if (season === 'Fall' || season === 'Winter') return { season: 'Spring', year: year + 1 };
  if (season === 'Spring') return includeSummer ? { season: 'Summer', year } : { season: 'Fall', year };
  return { season: 'Fall', year };
}

/** The latest of some terms, e.g. the in-progress semester. */
export function latestTerm(terms) {
  const order = { Spring: 1, Summer: 2, Fall: 3, Winter: 4 };
  let best = null;
  let bestKey = -1;
  terms.forEach(term => {
    const m = TERM_RE.exec(term || '');
    if (!m) return;
    const key = Number(m[2]) * 10 + order[m[1]];
    if (key > bestKey) { bestKey = key; best = term; }
  });
  return best;
}

const words = text => text.toLowerCase().replace(/&/g, 'and').replace(/[^a-z0-9 ]+/g, ' ').split(/\s+/).filter(Boolean);

/** The GradPath major for an audit's major (+ concentration), or null when
 *  it isn't clear which one. *options*: [{key, name}] */
export function matchMajor(options, majorName, concentration = '') {
  if (!majorName) return null;
  const want = words(majorName).join(' ');
  const candidates = options.filter(option => {
    const base = words(option.name.split(',')[0]).join(' ');
    return base === want || base.startsWith(`${want} `) || want.startsWith(`${base} `) || base === want.replace(/ major$/, '');
  });
  if (!candidates.length) return null;
  if (candidates.length === 1) return candidates[0];
  if (concentration) {
    const conc = words(concentration).join(' ');
    const hits = candidates.filter(option => words(option.name).join(' ').includes(conc));
    if (hits.length === 1) return hits[0];
  }
  // Only one without a concentration: the plain major
  const plain = candidates.filter(option => !/concentration/i.test(option.name));
  return plain.length === 1 && !concentration ? plain[0] : null;
}

/** The GradPath minor for an audit's minor name, or null. */
export function matchMinor(options, minorName) {
  if (!minorName) return null;
  const want = words(minorName).join(' ').replace(/ minor$/, '');
  return options.find(option => words(option.name).join(' ').replace(/ minor$/, '') === want) || null;
}

/** The "Overall GPA" in the audit's header (e.g. "3.250"), or null. */
function headerGpa(headerLines) {
  const text = headerLines.map(l => l.map(i => i.str).join(' ')).join(' ');
  if (!/overall gpa/i.test(text)) return null;
  for (const line of headerLines) {
    for (const item of line) {
      if (/^[0-4]\.\d{2,3}$/.test(item.str)) return Number(item.str);
    }
  }
  return null;
}

// MTSU grade points (plus/minus)
const POINTS = {
  A: 4, 'A-': 3.67, 'B+': 3.33, B: 3, 'B-': 2.67, 'C+': 2.33, C: 2, 'C-': 1.67, 'D+': 1.33, D: 1, 'D-': 0.67, F: 0,
};
const TERM_ORDER = { Spring: 1, Summer: 2, Fall: 3, Winter: 4 };
const termKey = term => {
  const m = TERM_RE.exec(term || '');
  return m ? Number(m[2]) * 10 + TERM_ORDER[m[1]] : 0;
};

/**
 * MTSU GPA hours from the audit's graded attempts: letter-graded MTSU
 * courses only (not transfer "T" grades, P, W or classes in progress), and a
 * repeated course counts once, at its latest grade. {hours, gpa} -- gpa is
 * what those attempts work out to, to check against the audit's own.
 */
export function gpaFromAttempts(attempts) {
  const latest = new Map();
  attempts.forEach(a => {
    if (!(a.grade in POINTS) || !(a.credits > 0)) return;
    const prev = latest.get(a.code);
    if (!prev || termKey(a.term) > termKey(prev.term)) latest.set(a.code, a);
  });
  let hours = 0;
  let points = 0;
  latest.forEach(a => { hours += a.credits; points += POINTS[a.grade] * a.credits; });
  return { hours, gpa: hours ? points / hours : null };
}
