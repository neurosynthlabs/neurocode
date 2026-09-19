/* Icons that data refers to BY NAME — nav items, agents, search hits, sidebar groups.
   An explicit registry instead of `import * as Icons from 'lucide-react'`: a namespace import
   defeats tree-shaking and drags the entire icon set into the entry chunk. Add a name here the
   moment a data file starts referencing it (scripts/smoke.mjs does not catch a missing one —
   it silently falls back to a circle). */
import {
  Activity, Blocks, Bot, Brain, BrainCircuit, Bug, Building2, Circle, Coins, Compass, Cpu, Crown, Database,
  Dna, Eye, FileClock, FileCode, FileText, FlaskConical, FolderKanban, Gauge, GitBranchPlus, GitCommit, GitMerge,
  History, KeyRound, LayoutDashboard, Library, Lightbulb, ListChecks, Mic, Microscope, Monitor, Network, Plug,
  Rocket, Scale, ScanEye, ScrollText, Server, Settings, ShieldAlert, ShieldCheck, Sparkles, SquareSlash, Table2,
  Users, UsersRound, Webhook, Workflow, type LucideIcon,
} from 'lucide-react';

export const ICONS: Record<string, LucideIcon> = {
  Activity, Blocks, Bot, Brain, BrainCircuit, Bug, Building2, Circle, Coins, Compass, Cpu, Crown, Database,
  Dna, Eye, FileClock, FileCode, FileText, FlaskConical, FolderKanban, Gauge, GitBranchPlus, GitCommit, GitMerge,
  History, KeyRound, LayoutDashboard, Library, Lightbulb, ListChecks, Mic, Microscope, Monitor, Network, Plug,
  Rocket, Scale, ScanEye, ScrollText, Server, Settings, ShieldAlert, ShieldCheck, Sparkles, SquareSlash, Table2,
  Users, UsersRound, Webhook, Workflow,
};
