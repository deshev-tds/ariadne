import type { ContextWindowPreview } from '$lib/apis/chats';

export const estimateDraftTokens = (draft: string): number => {
	if (!draft) {
		return 0;
	}

	return Math.max(1, Math.floor(draft.length / 4));
};

export const clampRatio = (value: number): number => {
	if (!Number.isFinite(value)) {
		return 0;
	}

	return Math.max(0, Math.min(1, value));
};

export const formatTokenCount = (value: number): string => {
	if (!Number.isFinite(value)) {
		return '0';
	}

	if (value >= 1000) {
		return new Intl.NumberFormat('en-US', {
			notation: 'compact',
			maximumFractionDigits: value >= 100000 ? 0 : 1
		}).format(value);
	}

	return Math.round(value).toString();
};

export const getConfidenceLabel = (confidence: string): string => {
	switch (confidence) {
		case 'exact':
			return 'Exact';
		case 'model_tokenizer':
			return 'Model tokenizer';
		case 'fallback':
			return 'Fallback estimate';
		default:
			return 'Approximate';
	}
};

export const buildContextWindowMetrics = (
	preview: ContextWindowPreview | null | undefined,
	draftPrompt: string
) => {
	const livePromptCap = Math.max(1, preview?.live_prompt_cap ?? 1);
	const currentTokens = Math.max(0, preview?.current_request_tokens ?? 0);
	const draftTokens = estimateDraftTokens(draftPrompt);
	const ghostTokens = currentTokens + draftTokens;
	const softTriggerTokens = preview?.soft_trigger_tokens ?? null;
	const hardTriggerTokens = preview?.hard_trigger_tokens ?? null;
	const confidence = preview?.token_count_confidence ?? 'approximate';
	const degraded = confidence === 'fallback' || confidence === 'approximate';
	const showBand = softTriggerTokens !== null && hardTriggerTokens !== null && hardTriggerTokens > 0;

	return {
		livePromptCap,
		currentTokens,
		draftTokens,
		ghostTokens,
		softTriggerTokens,
		hardTriggerTokens,
		degraded,
		showBand,
		currentRatio: clampRatio(currentTokens / livePromptCap),
		ghostRatio: clampRatio(ghostTokens / livePromptCap),
		softRatio:
			softTriggerTokens === null ? 0 : clampRatio(softTriggerTokens / livePromptCap),
		hardRatio:
			hardTriggerTokens === null ? 0 : clampRatio(hardTriggerTokens / livePromptCap)
	};
};
