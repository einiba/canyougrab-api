/**
 * Export plans.config.ts to JSON for consumption by the Python backend.
 * Usage: npx tsx scripts/export-plans-json.ts > backend/plans_config.json
 */
import { PLANS } from "../shared/plans.config.js";

const output: Record<string, any> = {};

for (const [id, plan] of Object.entries(PLANS)) {
  output[id] = {
    id: plan.id,
    name: plan.name,
    monthlyPrice: plan.monthlyPrice,
    monthlyLimit: plan.monthlyLimit,
    hourlyLimit: plan.hourlyLimit,
    domainCap: plan.domainCap,
    isActive: plan.isActive,
    isFree: plan.isFree,
    requiresCard: plan.requiresCard,
    stripe: plan.stripe,
  };
}

console.log(JSON.stringify(output, null, 2));
