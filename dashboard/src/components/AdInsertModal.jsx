import React from 'react';
import { Loader2 } from 'lucide-react';
import Modal from './ui/Modal';

function fmt(sec) {
    const s = Math.max(0, Math.round(Number(sec) || 0));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, '0')}`;
}

export default function AdInsertModal({
    isOpen, onClose, onGenerate, onRemove, isProcessing, plan, start, onStartChange,
}) {
    if (!isOpen) return null;

    const empty = !plan || plan.error;
    const fits = plan && plan.valid_end > plan.valid_start;
    const min = fits ? plan.valid_start : 0;
    const max = fits ? plan.valid_end : 1;
    const value = Math.min(Math.max(start ?? min, min), max);
    const end = value + (plan?.ad_duration || 0);

    return (
        <Modal isOpen={isOpen} onClose={onClose} eyebrow="AUTOAUDIT" title="add ads" size="sm">
            {empty ? (
                <p className="text-sm text-muted leading-relaxed">
                    {plan?.error || 'Upload clip iklan 2–5 detik di Clip Generator → advanced options.'}
                </p>
            ) : (
                <div className="space-y-4">
                    <p className="text-xs text-ink2">
                        Aktif: {plan.source_name} ({fmt(plan.ad_duration)})
                    </p>
                    {!fits ? (
                        <p className="text-xs text-danger">Clip terlalu pendek untuk bumper ini.</p>
                    ) : (
                        <>
                            <div>
                                <div className="flex items-center justify-between mb-2">
                                    <p className="eyebrow">start</p>
                                    <span className="readout">{fmt(value)}–{fmt(end)}</span>
                                </div>
                                <input
                                    type="range"
                                    min={min}
                                    max={max}
                                    step={0.1}
                                    value={value}
                                    onChange={(e) => onStartChange(parseFloat(e.target.value))}
                                    className="w-full accent-[var(--color-accent)]"
                                />
                            </div>
                            <p className="text-[11px] text-muted leading-relaxed">
                                Satu bumper setelah hook. Suara podcast tetap. Setelah recut/subtitle, generate lagi jika iklannya hilang.
                            </p>
                        </>
                    )}
                    {plan.ad_insert && onRemove && (
                        <button
                            type="button"
                            onClick={onRemove}
                            disabled={isProcessing}
                            className="text-xs underline underline-offset-2 text-muted hover:text-ink2"
                        >
                            hapus iklan dari clip
                        </button>
                    )}
                </div>
            )}
            <div className="flex gap-2 mt-5">
                <button type="button" onClick={onClose} className="btn-ghost">cancel</button>
                <button
                    type="button"
                    disabled={isProcessing || empty || !fits}
                    onClick={() => onGenerate(value)}
                    className="btn-primary flex-1"
                >
                    {isProcessing ? <Loader2 size={16} className="animate-spin" /> : (plan?.ad_insert ? 'replace' : 'generate')}
                </button>
            </div>
        </Modal>
    );
}
