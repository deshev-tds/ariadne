type NormalizeModelSelectionOptions = {
	preserveEmpty?: boolean;
	fallbackToEmpty?: boolean;
};

type HistoryMessageWithModels = {
	models?: unknown;
	childrenIds?: string[];
	model?: string;
	modelIdx?: number;
	[key: string]: unknown;
};

type HistoryWithModelMessages = {
	messages?: Record<string, HistoryMessageWithModels>;
};

export const normalizeModelSelection = (
	value: unknown,
	{ preserveEmpty = false, fallbackToEmpty = true }: NormalizeModelSelectionOptions = {}
): string[] => {
	const source = Array.isArray(value) ? value : [value ?? ''];
	const seen = new Set<string>();
	const modelIds: string[] = [];
	let hasEmpty = false;

	for (const item of source) {
		const modelId = typeof item === 'string' ? item.trim() : '';

		if (!modelId) {
			hasEmpty = true;
			continue;
		}

		if (seen.has(modelId)) {
			continue;
		}

		seen.add(modelId);
		modelIds.push(modelId);
	}

	if (preserveEmpty && hasEmpty) {
		modelIds.push('');
	}

	return modelIds.length > 0 ? modelIds : fallbackToEmpty ? [''] : [];
};

export const normalizeHistoryModelSelections = <T extends HistoryWithModelMessages>(
	history: T
): T => {
	const messages = history?.messages;
	if (!messages) {
		return history;
	}

	for (const message of Object.values(messages)) {
		if (!Array.isArray(message?.models)) {
			continue;
		}

		const originalModels = message.models;
		const normalizedModels = normalizeModelSelection(originalModels);

		if (JSON.stringify(originalModels) === JSON.stringify(normalizedModels)) {
			continue;
		}

		const normalizedIndexByModelId = new Map<string, number>();
		normalizedModels.forEach((modelId, modelIdx) => {
			if (modelId) {
				normalizedIndexByModelId.set(modelId, modelIdx);
			}
		});

		const modelIndexMap = new Map<number, number>();
		originalModels.forEach((modelId, modelIdx) => {
			if (typeof modelId !== 'string') {
				return;
			}

			const normalizedModelIdx = normalizedIndexByModelId.get(modelId.trim());
			if (normalizedModelIdx !== undefined) {
				modelIndexMap.set(modelIdx, normalizedModelIdx);
			}
		});

		message.models = normalizedModels;

		for (const childId of message.childrenIds ?? []) {
			const childMessage = messages[childId];
			if (!childMessage) {
				continue;
			}

			if (typeof childMessage.modelIdx === 'number') {
				const normalizedModelIdx = modelIndexMap.get(childMessage.modelIdx);
				if (normalizedModelIdx !== undefined) {
					childMessage.modelIdx = normalizedModelIdx;
				}
			} else if (typeof childMessage.model === 'string') {
				const normalizedModelIdx = normalizedModels.indexOf(childMessage.model);
				if (normalizedModelIdx >= 0) {
					childMessage.modelIdx = normalizedModelIdx;
				}
			}
		}
	}

	return history;
};
