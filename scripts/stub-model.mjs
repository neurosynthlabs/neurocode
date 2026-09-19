#!/usr/bin/env node
// A model for the end-to-end run: an OpenAI-compatible chat-completions server that answers every prompt
// NeuroCode sends with a small, deterministic, schema-valid reply.
//
// It exists so the checks can walk the real path — the gateway, a lane, the ledger, the parsers — without
// a key, without the network, and without the app keeping a canned answer of its own for when no model is
// there. The app has no such fallback any more; this is where a scripted answer belongs: in the tests.
//
//   node scripts/stub-model.mjs [port]         serve on its own, for poking at by hand
//   import { startStubModel } from './stub-model.mjs'
//
// Point a lane at it with the environment the lanes already read:
//   GROQ_API_KEY=stub  NEUROCODE_GROQ_URL=http://127.0.0.1:<port>
import http from 'node:http';
import { fileURLToPath } from 'node:url';

/** Which prompt this is, told apart by the first words of its system message. */
const KINDS = [
  ['compile', 'You are the requirement compiler'],
  ['ask', "You are NeuroCode's memory"],
  ['brainstorm', 'You are a sharp product partner'],
  ['extract', 'You extract durable facts'],
  ['edit', 'You are a senior engineer working inside NeuroCode'],
  ['review', 'You are the reviewer inside NeuroCode'],
  ['decompose', 'You plan research inside NeuroCode'],
  ['angle', 'You answer one research sub-question'],
  ['synthesis', 'You write the conclusion of a research'],
  ['judge', 'You are a strict evaluator'],
  ['chat', 'You are NeuroCode, working inside an engineering workspace'],
];

const kindOf = (system) => KINDS.find(([, start]) => system.startsWith(start))?.[0] ?? 'unknown';
const firstLine = (text) => (text.split('\n').find((l) => l.trim()) ?? '').trim();

function reply(kind, system, user) {
  switch (kind) {
    case 'compile': {
      // The requirement is echoed into the plan; files the prompt names become the files it affects.
      const requirement = (/Requirement:\s*([^\n]+)/i.exec(user)?.[1] ?? firstLine(user)).slice(0, 200);
      const files = [...new Set(user.match(/\b[\w./-]+\.(?:py|ts|tsx|js|go|cs|java|sql)\b/g) ?? [])].slice(0, 4);
      return {
        title: requirement.slice(0, 80) || 'Change',
        businessRequirement: requirement,
        technicalRequirement: `Change ${files[0] ?? 'the code the requirement names'} so that: ${requirement}`,
        affectedModules: [...new Set(files.map((f) => f.split('/')[0]))],
        affectedFiles: files,
        risk: 'LOW', confidence: 72, priority: 'NORMAL', layers: ['backend'],
        steps: [{ label: `Change ${files[0] ?? 'the code'}`, agent: 'Backend Engineer', detail: requirement }],
        testPlan: ['Run the project\'s own test command'],
        openQuestions: ['Should the change keep the old behaviour behind a flag?', 'Who signs off the release?'],
      };
    }
    case 'ask': {
      const refs = [...new Set(user.match(/MEM-\d+/g) ?? [])].slice(0, 2);
      return refs.length
        ? { answer: `From memory: ${refs.map((r) => `[${r}]`).join(' and ')} bear on this.`, citations: refs }
        : { answer: 'Nothing in memory answers this yet.', citations: [] };
    }
    case 'brainstorm': {
      const idea = firstLine(user.replace(/^Idea:\s*/im, '')).slice(0, 120);
      return {
        title: idea.slice(0, 60) || 'An idea', problem: `Today: ${idea}`, audience: 'The people who asked for it',
        value: 'Less manual work', mvp: ['The smallest version that proves it'], risks: ['Nobody needs it yet'],
        metrics: ['People who use it twice'], questions: ['Who asked for this?'],
        roadmap: [{ phase: 'First', items: ['Build the smallest version'] }],
      };
    }
    case 'extract': {
      const sentences = user.split(/(?<=[.!?])\s+/).filter((s) => /\b(must|always|never|only)\b/i.test(s)).slice(0, 8);
      return {
        facts: sentences.map((s) => ({
          title: s.split(/\s+/).slice(0, 5).join(' '), body: s.trim(), category: 'business_rules',
          confidence: 'HIGH', reason: 'Stated in the pasted text.',
        })),
      };
    }
    case 'edit': {
      // Every file it was shown comes back changed by one line, so the run has a real diff to test and review.
      // Split on the `--- path` headers the runtime writes, rather than a multiline regex whose `$` would stop
      // at the end of a file's first line.
      const shown = user.split(/^--- (\S+)$/m).slice(1);
      const files = [];
      for (let i = 0; i + 1 < shown.length && files.length < 2; i += 2) {
        files.push({ path: shown[i], content: `${shown[i + 1].replace(/^\n/, '').replace(/\s+$/, '')}\n# changed by the stub model\n` });
      }
      return { summary: files.length ? `Changed ${files.map((f) => f.path).join(', ')}` : 'Nothing to change', files, notes: [] };
    }
    case 'review':
      return { findings: [], verdict: 'The change is small and does what the step says.' };
    case 'decompose': {
      const q = (/Question:\s*([\s\S]+)$/i.exec(user)?.[1] ?? firstLine(user)).trim().slice(0, 160);
      return { questions: [`${q} — in the code`, `${q} — in the documentation`] };
    }
    case 'angle': {
      const refs = [...user.matchAll(/^\[[a-z]+ · ([^\]]+)\]$/gm)].map((m) => m[1]).slice(0, 2);
      return refs.length
        ? { finding: `The pieces ${refs.join(' and ')} answer it.`, citations: refs }
        : { finding: 'The pieces do not answer this.', citations: [] };
    }
    case 'synthesis':
      return { summary: 'The findings agree.', recommendation: 'Keep it as it is.', risks: [], alternatives: [],
               architecture: '', gaps: [] };
    case 'judge':
      return { pass: true, reason: 'The answer meets the rubric.' };
    case 'chat':
      return { answer: 'The stub model answers without reading anything.' };
    default:
      return { answer: 'The stub model does not know this prompt.', note: firstLine(system).slice(0, 80) };
  }
}

export function startStubModel(port = 0) {
  const seen = [];
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', () => {
      if (req.method !== 'POST' || !req.url.endsWith('/chat/completions')) {
        res.writeHead(404, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: { message: `no ${req.method} ${req.url} here` } }));
        return;
      }
      const { messages = [] } = JSON.parse(body || '{}');
      const system = messages.find((m) => m.role === 'system')?.content ?? '';
      const user = messages.filter((m) => m.role === 'user').map((m) => m.content).join('\n\n');
      const kind = kindOf(system);
      seen.push(kind);
      const content = JSON.stringify(reply(kind, system, user));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }],
        usage: { prompt_tokens: Math.ceil((system.length + user.length) / 4), completion_tokens: Math.ceil(content.length / 4) },
      }));
    });
  });
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => {
    resolve({ url: `http://127.0.0.1:${server.address().port}`, seen, close: () => new Promise((r) => server.close(r)) });
  }));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const stub = await startStubModel(Number(process.argv[2] ?? 0));
  console.log(`stub model on ${stub.url}`);
}
