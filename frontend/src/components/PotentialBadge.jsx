/**
 * PotentialBadge — explainability chip shown on BO pick cards.
 *
 * Displays Potential (EI) inline on the card face. Full GP stats
 * (Probable Score, Uncertainty, Nearest known ads) are in the click modal.
 *
 * Feature flag: set SHOW_POTENTIAL_BADGE = false to hide everywhere.
 *
 * NOTE: EI is a GP surrogate estimate, not an observed metric. Modal GP
 * scores use a rank-transformed scale; do not compare to real CTR/ROAS.
 */

export const SHOW_POTENTIAL_BADGE = true;

export default function PotentialBadge({ pick }) {
  if (!SHOW_POTENTIAL_BADGE) return null;

  const { ei_score, gpr_mean } = pick;
  if (ei_score == null && gpr_mean == null) return null;

  return (
    <span className="potential-badge">
      <span className="potential-badge-label">Potential</span>
      <span className="potential-badge-value">
        {ei_score != null ? ei_score.toFixed(4) : "—"}
      </span>
    </span>
  );
}
