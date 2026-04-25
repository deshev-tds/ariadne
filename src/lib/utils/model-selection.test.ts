import { describe, expect, it } from 'vitest';

import { normalizeHistoryModelSelections, normalizeModelSelection } from './model-selection';

describe('model selection normalization', () => {
	it('deduplicates selected models while preserving order', () => {
		expect(normalizeModelSelection(['llama', 'llama', 'qwen', 'llama'])).toEqual(['llama', 'qwen']);
	});

	it('can preserve one empty slot for the live selector', () => {
		expect(normalizeModelSelection(['llama', '', 'llama', ''], { preserveEmpty: true })).toEqual([
			'llama',
			''
		]);
	});

	it('normalizes legacy per-message model arrays and remaps child model indexes', () => {
		const history = {
			messages: {
				user: {
					id: 'user',
					role: 'user',
					models: ['qwen', 'qwen', 'llama'],
					childrenIds: ['a', 'b', 'c']
				},
				a: { id: 'a', role: 'assistant', parentId: 'user', model: 'qwen', modelIdx: 0 },
				b: { id: 'b', role: 'assistant', parentId: 'user', model: 'qwen', modelIdx: 1 },
				c: { id: 'c', role: 'assistant', parentId: 'user', model: 'llama', modelIdx: 2 }
			},
			currentId: 'c'
		};

		expect(normalizeHistoryModelSelections(history)).toBe(history);
		expect(history.messages.user.models).toEqual(['qwen', 'llama']);
		expect(history.messages.a.modelIdx).toBe(0);
		expect(history.messages.b.modelIdx).toBe(0);
		expect(history.messages.c.modelIdx).toBe(1);
	});
});
