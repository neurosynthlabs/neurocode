/* The public demo has no API, so its AI features answer here, with the same rules the API falls back
   on when no model is configured (server/app/ai/features.py). Every answer is labelled offline, so
   nobody mistakes a rule for a model. */
import type { AskAnswer, BrainstormDoc, Brief, Extracted, FactCandidate } from '@/lib/api';
import type { MemoryCategory, MemoryFact } from '@/types';

// Words that carry no meaning for relevance, English and Hinglish alike.
const STOP = new Set(`the and for with when that this from into are was were not but should must have has had then than
your you our they them its also only just like very what which who why how all any can could would will may might been
being does did done fix make need needs want please mein hai hain raha rahi rahe karo kar ko ka ki ke pe par se aur nahi
ho yeh woh tha thi abhi bhi jo kya kuch sab`.split(/\s+/));

const offline = (model: string) => ({ provider: 'rules' as const, model, ms: 0 });

function firstSentence(text: string, limit = 220) {
  const s = text.trim().split(/(?<=[.!?])\s/)[0];
  return s.length <= limit ? s : `${s.slice(0, limit).trimEnd()}…`;
}

const stamp = (d: Date) =>
  `${String(d.getDate()).padStart(2, '0')} ${d.toLocaleString('en', { month: 'short' })} ${d.toTimeString().slice(0, 5)}`;

export function ask(question: string, memory: MemoryFact[], projectId?: string): AskAnswer {
  const words = [...new Set((question.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter((w) => w.length >= 3 && !STOP.has(w)))];
  const top = memory
    .filter((f) => !projectId || f.projectId === projectId || f.projectId === 'global')
    .map((f) => {
      const text = `${f.ref} ${f.title} ${f.body} ${f.reason} ${f.tags.join(' ')}`.toLowerCase();
      return { f, score: words.filter((w) => text.includes(w)).length };
    })
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score || b.f.strength - a.f.strength)
    .slice(0, 4)
    .map((x) => x.f);
  if (!top.length) {
    return {
      ...offline('memory search'), citations: [],
      answer: 'Nothing in memory matches that yet. Try other words, or add what you know with Memory → Add from text.',
    };
  }
  return {
    ...offline('memory search'),
    answer: `Here is what memory holds on that:\n${top.map((f) => `• [${f.ref}] ${f.title}. ${firstSentence(f.body)}`).join('\n')}`,
    citations: top.map((f) => ({ ref: f.ref, title: f.title })),
  };
}

function brief(idea: string): Brief {
  const title = firstSentence(idea, 80).replace(/[ .]+$/, '');
  return {
    title: title.charAt(0).toUpperCase() + title.slice(1),
    problem: `As you put it: “${idea.trim()}”. The offline template keeps your words; a model sharpens them.`,
    audience: 'Name the one person who feels this most, and what they do today instead.',
    value: 'Say what gets faster, cheaper or safer for them, and how you would see it happen.',
    mvp: ['One flow, end to end, for one kind of user', 'The smallest change that proves people use it',
      'Measured from the first day, so usage is visible'],
    risks: ['Nobody feels the problem enough to change their habit', 'The data or access it needs is not available',
      'It duplicates something people already have'],
    metrics: ['Weekly users of the new flow', 'Time saved per use, before and after', 'Errors or tickets it removes'],
    questions: ['Who asked for this, and what exactly did they say?', 'What happens if we do nothing for three months?',
      'What is the one thing it must not break?'],
    roadmap: [
      { phase: 'Week 1', items: ['Talk to three users', 'Sketch the one flow'] },
      { phase: 'Weeks 2–3', items: ['Build the thin version', 'Ship it behind a flag'] },
      { phase: 'Week 4', items: ['Measure it', 'Decide: grow, change or stop'] },
    ],
  };
}

export function brainstorm(idea: string, projectId: string | null, n: number, by: string): BrainstormDoc {
  const at = new Date();
  return {
    id: `b${n}-${at.getTime()}`, ref: `IDEA-${n}`, idea: idea.trim(), projectId, brief: brief(idea),
    compiler: offline('offline template'), by, createdAt: stamp(at),
  };
}

const POLICY = /\b(must|never|always|should|shall|only|cannot|can't|not allowed|required|mandatory|rule|policy|decided|agreed|deadline|hona chahiye|nahi karna|kabhi nahi|zaroori|mat karo|chahiye)\b/i;
const HINTS: [MemoryCategory, RegExp][] = [
  ['decisions', /\b(decided|agreed|decision|we will|finali[sz]ed|tay hua)\b/i],
  ['bugs', /\b(bug|error|fails?|broken|crash|issue|galat)\b/i],
  ['database', /\b(table|column|sp_\w+|procedure|database|index|schema|trans_\w+|mst_\w+)\b/i],
  ['preferences', /\b(prefers?|likes?|hates?|wants?|pasand)\b/i],
  ['business_rules', /\b(must|never|always|only|rule|policy|tax|gst|invoice|price|rounding|zaroori|chahiye)\b/i],
];

export function extract(text: string): Extracted {
  const facts: FactCandidate[] = [];
  for (const raw of text.split(/(?<=[.!?])\s+|\n+/)) {
    const s = raw.trim().replace(/^[-•*\s]+|[-•*\s]+$/g, '');
    if (s.length <= 12 || !POLICY.test(s)) continue;
    facts.push({
      title: s.length <= 90 ? s : `${s.slice(0, 88).trimEnd()}…`, body: s,
      category: HINTS.find(([, rx]) => rx.test(s))?.[0] ?? 'project', confidence: 'MEDIUM',
      reason: 'Picked by the offline rules: the sentence states a rule, a decision or a constraint.',
    });
    if (facts.length === 8) break;
  }
  return { ...offline('offline rules'), facts };
}
