import { ArrowRight, CheckCircle2, LayoutDashboard, Sparkles } from 'lucide-react';
import Modal from './ui/Modal';
import StepIndicator from './ui/StepIndicator';

const STEPS = ['Add a video', 'Generate', 'Your clips'];

function stepIndex(jobStatus) {
  if (jobStatus === 'processing') return 1;
  if (jobStatus === 'complete') return 2;
  return 0;
}

function stepCopy(jobStatus) {
  if (jobStatus === 'processing') return 'Hang on — Clip Generator is finding the moments and cutting vertical shorts.';
  if (jobStatus === 'error') return 'That run failed. Try another YouTube link or file. The other tools stay locked until one job finishes.';
  if (jobStatus === 'complete') return 'Clips are ready.';
  return 'Paste a YouTube link or drop a video below, tick the rights box, then generate.';
}

/**
 * First-login Clip Generator tutorial.
 *  - intro: blocking modal (Start / skip)
 *  - coach: non-blocking card that tracks idle → processing → complete
 *  - celebrate: modal after the first successful job
 *
 * QA: open #app?tutorial=1 to force the intro without a fresh signup.
 */
export default function ClipTutorial({ phase, jobStatus, onStart, onSkip, onDismissCelebrate }) {
  if (phase === 'intro') {
    return (
      <Modal isOpen hideClose eyebrow="FIRST CLIPS" title="Let's make your first shorts" size="md">
        <div className="flex items-start gap-3 mb-4">
          <div className="w-10 h-10 rounded-input bg-paper3 flex items-center justify-center shrink-0">
            <LayoutDashboard size={18} className="text-brass" />
          </div>
          <p className="text-sm text-ink2 leading-relaxed">
            People come here for <b className="text-ink font-medium">Clip Generator</b>: a long video in,
            vertical shorts out. That is the only tool unlocked on this first run.
          </p>
        </div>
        <ol className="text-sm text-muted space-y-2 mb-6 list-decimal pl-5">
          <li>Paste a YouTube link or upload a file</li>
          <li>Generate</li>
          <li>Watch the clips</li>
        </ol>
        <button type="button" onClick={onStart} className="btn-primary w-full">
          Start with Clip Generator <ArrowRight size={16} />
        </button>
        <button
          type="button"
          onClick={onSkip}
          className="w-full mt-2 text-muted hover:text-ink text-sm lowercase py-2 transition-colors"
        >
          I'll explore later
        </button>
      </Modal>
    );
  }

  if (phase === 'coach') {
    const current = stepIndex(jobStatus);
    return (
      <div
        className="fixed z-[80] left-3 right-3 md:left-auto md:right-6 md:w-[22rem]
          bottom-[4.75rem] md:bottom-6 card p-4 shadow-[0_12px_40px_rgba(0,0,0,0.35)]"
        role="status"
      >
        <p className="eyebrow mb-3">First clips</p>
        <StepIndicator steps={STEPS} current={current} />
        <p className={`text-sm leading-relaxed mt-3 ${jobStatus === 'error' ? 'text-danger' : 'text-muted'}`}>
          {stepCopy(jobStatus)}
        </p>
        <button
          type="button"
          onClick={onSkip}
          className="mt-3 text-xs lowercase text-muted hover:text-ink transition-colors"
        >
          skip — unlock the rest
        </button>
      </div>
    );
  }

  if (phase === 'celebrate') {
    return (
      <Modal isOpen onClose={onDismissCelebrate} hideClose eyebrow="DONE" title="You made your first clips" size="sm">
        <div className="text-center py-2">
          <div className="inline-flex p-3 bg-paper3 rounded-full text-ok mb-4">
            <CheckCircle2 size={28} />
          </div>
          <p className="text-sm text-ink2 leading-relaxed mb-1">
            That is the whole product, on one video.
          </p>
          <p className="text-sm text-muted leading-relaxed mb-6">
            The other tools are unlocked. Come back to Clip Generator whenever you have another long video.
          </p>
          <button type="button" onClick={onDismissCelebrate} className="btn-primary w-full">
            <Sparkles size={16} /> See my clips
          </button>
        </div>
      </Modal>
    );
  }

  return null;
}
