import { describe, expect, it } from 'vitest';

import {
	buildContextWindowMetrics,
	estimateDraftTokens,
	formatTokenCount,
	getConfidenceLabel
} from './contextWindow';

describe('context window helpers', () => {
	it('estimates draft tokens with the lightweight overlay heuristic', () => {
		expect(estimateDraftTokens('')).toBe(0);
		expect(estimateDraftTokens('12345678')).toBe(2);
	});

	it('builds aggregate ring metrics with a ghost overlay', () => {
		const metrics = buildContextWindowMetrics(
			{
				model_id: 'small',
				model_name: 'Small',
				live_prompt_cap: 32000,
				live_prompt_cap_source: 'probe:slots',
				current_request_tokens: 16000,
				soft_trigger_tokens: 22000,
				hard_trigger_tokens: 26000,
				summary_active: true,
				compaction_version: 2,
				maintenance_enabled: true,
				token_count_confidence: 'fallback',
				token_count_source: 'tiktoken',
				limiting_model_id: 'small',
				limiting_model_name: 'Small',
				active_main_model_ids: ['small', 'large'],
				multi_model: true,
				model_previews: []
			},
			'12345678'
		);

		expect(metrics.currentRatio).toBe(0.5);
		expect(metrics.ghostTokens).toBe(16002);
		expect(metrics.softRatio).toBeCloseTo(22000 / 32000);
		expect(metrics.degraded).toBe(true);
	});

	it('formats confidence labels and token counts for tooltip copy', () => {
		expect(getConfidenceLabel('model_tokenizer')).toBe('Model tokenizer');
		expect(getConfidenceLabel('fallback')).toBe('Fallback estimate');
		expect(formatTokenCount(65536)).toMatch(/65/);
	});
});
