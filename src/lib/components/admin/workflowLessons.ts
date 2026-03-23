export const WORKFLOW_LESSONS_TABS = ['repeated', 'observed', 'promoted'] as const;

export type WorkflowLessonsTab = (typeof WORKFLOW_LESSONS_TABS)[number];

export const normalizeWorkflowLessonsTab = (
	value?: string | null
): WorkflowLessonsTab => {
	if (value && WORKFLOW_LESSONS_TABS.includes(value as WorkflowLessonsTab)) {
		return value as WorkflowLessonsTab;
	}
	return 'repeated';
};

export const getChatIdFromSourceTurnId = (sourceTurnId?: string | null): string | null => {
	const value = String(sourceTurnId ?? '').trim();
	if (!value) return null;
	const [chatId] = value.split(':', 1);
	return chatId?.trim() || null;
};

export const getChatHrefFromSourceTurnId = (sourceTurnId?: string | null): string | null => {
	const chatId = getChatIdFromSourceTurnId(sourceTurnId);
	return chatId ? `/c/${chatId}` : null;
};
