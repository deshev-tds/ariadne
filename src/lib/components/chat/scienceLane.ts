export type ScienceLaneSystemTerminal = {
	id?: string | null;
};

export type ScienceLaneDirectTerminal = {
	url?: string | null;
	enabled?: boolean | null;
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
