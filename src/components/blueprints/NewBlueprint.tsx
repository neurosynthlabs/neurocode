import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { FileQuestion, Loader2, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { Field, Tag, Wizard, type WizardStep } from '@/components/os';
import { ApiError } from '@/lib/api';
import { blueprintsApi, type Answers, type Catalogue, type BlueprintDoc, type Question, type TemplateSummary } from '@/lib/live/blueprints';
import { cn } from '@/lib/utils';

/* "New blueprint": the idea in a person's own words, a short questionnaire (every question skippable), then
   a starting point — the catalogue's templates ranked by how well they fit the answers, with the answers
   that matched and the ones that argued against, or a blank page, or one of your own templates. */

const PRODUCT = ['productType', 'audience', 'scale', 'traffic', 'data', 'compliance', 'needs'];
const TEAM = ['team', 'teamSize', 'hosting', 'budget', 'timeline'];
const BLANK = '__blank__';

const failed = (e: unknown) => (e instanceof ApiError ? e.message : 'The API did not answer.');

export function Choices({ q, value, onChange }: { q: Question; value: string | string[] | undefined; onChange: (v: string | string[] | undefined) => void }) {
  const many = q.kind === 'many';
  const chosen = new Set(Array.isArray(value) ? value : value ? [value] : []);
  const toggle = (id: string) => {
    if (many) {
      const next = chosen.has(id) ? [...chosen].filter((x) => x !== id) : [...chosen, id];
      onChange(next.length ? next : undefined);
    } else onChange(chosen.has(id) ? undefined : id);
  };
  return (
    <fieldset className="min-w-0">
      <legend className="mb-2 flex w-full items-baseline justify-between gap-3 text-[13.5px] font-medium text-ink-2">
        <span>{q.question}</span>
        <span className="shrink-0 text-[12px] font-normal text-dim">{many ? 'Pick any' : 'Pick one'} · or skip</span>
      </legend>
      <div className="flex flex-wrap gap-1.5">
        {(q.options ?? []).map((o) => (
          <button key={o.id} type="button" aria-pressed={chosen.has(o.id)} onClick={() => toggle(o.id)}
            className={cn('rounded-full border px-3 py-1.5 text-[13px] transition-colors',
              chosen.has(o.id) ? 'border-brand/50 bg-brand/10 text-ink' : 'border-line bg-surface-2/40 text-soft hover:text-ink')}>
            {o.label}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function FitLine({ t }: { t: TemplateSummary }) {
  if (!t.fit) return null;
  const { fits, against, considered, conditions } = t.fit;
  if (considered === 0) return <p className="mt-1.5 text-[12px] text-dim">None of your answers speak to its {conditions} conditions.</p>;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {fits.map((f) => <Tag key={`f${f.says}`} tone="ok" className="whitespace-normal">{f.says}</Tag>)}
      {against.map((f) => <Tag key={`a${f.says}`} tone="warn" className="whitespace-normal">not ideal: {f.says}</Tag>)}
    </div>
  );
}

export function NewBlueprint({ open, onOpenChange, catalogue, startFrom, onCreated }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  catalogue: Catalogue;
  /** A template chosen before the wizard opened (from the template bank). */
  startFrom?: string | null;
  onCreated: (bp: BlueprintDoc) => void;
}) {
  // The page mounts a fresh wizard for every opening, so each one starts empty — with the template the
  // bank handed over, when it did.
  const [name, setName] = useState('');
  const [answers, setAnswers] = useState<Answers>({});
  const [picked, setPicked] = useState<string>(startFrom ?? BLANK);
  const [ranked, setRanked] = useState<{ catalogue: TemplateSummary[]; mine: TemplateSummary[] } | null>(null);
  const [ranking, setRanking] = useState(false);
  const [busy, setBusy] = useState(false);
  const questions = useMemo(() => new Map(catalogue.questions.map((q) => [q.id, q])), [catalogue.questions]);

  const set = (id: string, v: string | string[] | undefined) =>
    setAnswers((a) => { const next = { ...a }; if (v === undefined || v === '') delete next[id]; else next[id] = v; return next; });

  const rank = async () => {
    setRanking(true);
    try {
      const got = await blueprintsApi.rank(answers);
      setRanked(got);
      // With no choice made yet, the best fit is offered — only when some answer actually matched it.
      setPicked((p) => (p !== BLANK || startFrom ? p : got.catalogue[0] && got.catalogue[0].fit && got.catalogue[0].fit.score > 0 ? got.catalogue[0].id : BLANK));
    } catch (e) {
      toast.error('The templates could not be ranked', { description: failed(e) });
    } finally {
      setRanking(false);
    }
  };

  const finish = async () => {
    setBusy(true);
    try {
      const bp = await blueprintsApi.create(name.trim(), picked === BLANK ? null : picked, answers);
      toast.success(`${bp.name} started`, { description: bp.templateName ? `From ${bp.templateName}` : 'From a blank page' });
      onOpenChange(false);
      onCreated(bp);
    } catch (e) {
      toast.error('Not created', { description: failed(e) });
    } finally {
      setBusy(false);
    }
  };

  const idea = questions.get('idea');
  const card = (t: TemplateSummary) => (
    <button key={t.id} type="button" onClick={() => setPicked(t.id)} aria-pressed={picked === t.id}
      className={cn('w-full rounded-xl border px-4 py-3 text-left transition-colors',
        picked === t.id ? 'border-brand/60 bg-brand/7' : 'border-line/70 bg-surface hover:bg-surface-2/60')}>
      <span className="flex items-start justify-between gap-3">
        <span className="min-w-0">
          <span className="block text-[14px] font-medium text-ink">{t.name}</span>
          <span className="mt-0.5 line-clamp-2 block text-[12.5px] text-soft">{t.summary || t.description}</span>
        </span>
        {t.fit && t.fit.considered > 0 && (
          <span className={cn('tnum shrink-0 rounded-full px-2 py-0.5 text-[12px] font-medium',
            t.fit.score > 0 ? 'bg-ok/12 text-ok' : t.fit.score < 0 ? 'bg-warn/12 text-warn' : 'bg-surface-3 text-soft')}
            title="Conditions met, minus conditions that argue against it">
            {t.fit.score > 0 ? `+${t.fit.score}` : t.fit.score}
          </span>
        )}
      </span>
      <FitLine t={t} />
    </button>
  );

  const steps: WizardStep[] = [
    {
      id: 'idea', title: 'The idea', hint: 'in your own words', valid: name.trim().length > 0, blocker: 'Give it a name',
      content: (
        <div className="space-y-4">
          <Field label="Name" value={name} onChange={setName} placeholder="Clinic bookings" autoFocus />
          {idea && (
            <label className="block">
              <span className="mb-1.5 block text-[12.5px] font-medium text-soft">{idea.question}</span>
              <textarea value={(answers.idea as string) ?? ''} onChange={(e) => set('idea', e.target.value)} rows={4}
                placeholder="Clinics book online; staff see the day; patients get reminders."
                className="focus-brand w-full resize-y rounded-lg border border-line bg-surface-2/60 px-3 py-2 text-[13.5px] text-ink placeholder:text-dim focus-visible:outline-none" />
              <span className="mt-1.5 block text-[12px] text-dim">{idea.hint}</span>
            </label>
          )}
        </div>
      ),
    },
    {
      id: 'product', title: 'The product', hint: 'every question skippable',
      content: <div className="space-y-5">{PRODUCT.map((id) => questions.get(id)).filter((q): q is Question => !!q).map((q) => (
        <Choices key={q.id} q={q} value={answers[q.id]} onChange={(v) => set(q.id, v)} />))}</div>,
    },
    {
      id: 'team', title: 'Team and limits', hint: 'skills, hosting, budget',
      content: <div className="space-y-5">{TEAM.map((id) => questions.get(id)).filter((q): q is Question => !!q).map((q) => (
        <Choices key={q.id} q={q} value={answers[q.id]} onChange={(v) => set(q.id, v)} />))}</div>,
    },
    {
      id: 'start', title: 'Starting point', hint: 'ranked by your answers',
      content: (
        <StartingPoint ranked={ranked} ranking={ranking} onRank={rank} picked={picked} setPicked={setPicked} card={card}
          answered={Object.keys(answers).filter((k) => k !== 'idea').length} />
      ),
    },
  ];

  return (
    <Wizard open={open} onOpenChange={onOpenChange} title="New blueprint" busy={busy} finishLabel="Start designing"
      description="Describe it, answer what you can, pick a starting point."
      steps={steps} onFinish={() => void finish()} className="sm:max-w-3xl" />
  );
}

function StartingPoint({ ranked, ranking, onRank, picked, setPicked, card, answered }: {
  ranked: { catalogue: TemplateSummary[]; mine: TemplateSummary[] } | null;
  ranking: boolean;
  onRank: () => Promise<void>;
  picked: string;
  setPicked: (id: string) => void;
  card: (t: TemplateSummary) => ReactNode;
  answered: number;
}) {
  // Ranked each time the step opens, from the answers as they stand then.
  const rank = useRef(onRank);
  useLayoutEffect(() => { rank.current = onRank; });
  useEffect(() => { void rank.current(); }, []);
  if (!ranked) {
    return <p className="flex items-center gap-2 py-8 text-[13px] text-dim"><Loader2 className="size-4 animate-spin" />{ranking ? 'Ranking the templates by your answers…' : 'The templates did not load.'}</p>;
  }
  return (
    <div className="space-y-5">
      <p className="text-[12.5px] text-dim">
        {answered === 0
          ? 'Nothing answered, so templates are listed unranked.'
          : `Ranked by your ${answered} answer${answered === 1 ? '' : 's'}: conditions met, minus those against.`}
      </p>
      <button type="button" onClick={() => setPicked(BLANK)} aria-pressed={picked === BLANK}
        className={cn('flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left transition-colors',
          picked === BLANK ? 'border-brand/60 bg-brand/7' : 'border-line/70 bg-surface hover:bg-surface-2/60')}>
        <FileQuestion className="size-4 shrink-0 text-dim" />
        <span>
          <span className="block text-[14px] font-medium text-ink">A blank page</span>
          <span className="block text-[12.5px] text-soft">Choose every layer yourself.</span>
        </span>
      </button>
      {ranked.mine.length > 0 && (
        <section>
          <h3 className="mb-2 text-[12.5px] font-medium text-dim">Your templates</h3>
          <div className="space-y-2">{ranked.mine.map(card)}</div>
        </section>
      )}
      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-[12.5px] font-medium text-dim"><Sparkles className="size-3.5" />The template bank</h3>
        <div className="space-y-2">{ranked.catalogue.map(card)}</div>
      </section>
    </div>
  );
}
