import { describe, expect, it } from 'vitest';

import {
	getChatHrefFromSourceTurnId,
	normalizeWorkflowLessonsTab
} from './workflowLessons';

describe('workflow lessons admin helpers', () => {
	it('normalizes tabs to supported values', () => {
		expect(normalizeWorkflowLessonsTab('repeated')).toBe('repeated');
		expect(normalizeWorkflowLessonsTab('observed')).toBe('observed');
		expect(normalizeWorkflowLessonsTab('unknown')).toBe('repeated');
		expect(normalizeWorkflowLessonsTab()).toBe('repeated');
	});

	it('derives chat hrefs from source turn ids', () => {
		expect(getChatHrefFromSourceTurnId('chat-123:msg-456')).toBe('/c/chat-123');
		expect(getChatHrefFromSourceTurnId('chat-123')).toBe('/c/chat-123');
		expect(getChatHrefFromSourceTurnId('')).toBeNull();
	});
});
