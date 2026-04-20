export type ScienceLaneSystemTerminal = {
	id?: string | null;
};

export type ScienceResearchMode = 'light' | 'deep';

export type ScienceLaneDirectTerminal = {
	url?: string | null;
	enabled?: boolean | null;
};

export const normalizeScienceResearchMode = (value: unknown): ScienceResearchMode =>
	value === 'deep' ? 'deep' : 'light';

export const normalizeScienceAttachedCorpora = (value: unknown): string[] => {
	const items = Array.isArray(value)
		? value
		: typeof value === 'string'
			? value
					.split(',')
					.map((item) => item.trim())
					.filter(Boolean)
			: [];

	const normalized = items
		.map((item) =>
			String(item ?? '')
				.trim()
				.toLowerCase()
		)
		.filter((item) => item === 'medicine');

	return [...new Set(normalized)];
};

export const resolveScienceLaneTerminalId = ({
	selectedTerminalId,
	systemTerminals,
	directTerminals
}: {
	selectedTerminalId: string | null | undefined;
	systemTerminals?: ScienceLaneSystemTerminal[] | null | undefined;
	directTerminals?: ScienceLaneDirectTerminal[] | null | undefined;
}): string | null => {
	const normalizedSelectedTerminalId = selectedTerminalId?.trim();
	if (normalizedSelectedTerminalId) {
		return normalizedSelectedTerminalId;
	}

	const systemTerminalId = (systemTerminals ?? [])
		.map((terminal) => terminal?.id?.trim())
		.find(Boolean);
	if (systemTerminalId) {
		return systemTerminalId;
	}

	const activeDirectTerminalUrl = (directTerminals ?? [])
		.filter((terminal) => terminal?.enabled)
		.map((terminal) => terminal?.url?.trim())
		.find(Boolean);
	if (activeDirectTerminalUrl) {
		return activeDirectTerminalUrl;
	}

	return null;
};
