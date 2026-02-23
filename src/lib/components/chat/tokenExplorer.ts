export type TokenBranchRequest = {
	source_message_id: string;
	fork_index: number;
	alt_rank: number;
};

export const applyTokenExplorerDefaults = (
	params: Record<string, any>,
	tokenExplorerEnabled: boolean
) => {
	const nextParams = { ...(params ?? {}) };
	if (!tokenExplorerEnabled) {
		return nextParams;
	}

	if (nextParams.logprobs === undefined && nextParams.log_probs === undefined) {
		nextParams.logprobs = true;
	}
	if (nextParams.top_logprobs === undefined) {
		nextParams.top_logprobs = 5;
	}

	return nextParams;
};

export const buildTokenBranchPayload = (
	sourceMessageId: string,
	forkIndex: number,
	altRank: number
): TokenBranchRequest => ({
	source_message_id: sourceMessageId,
	fork_index: forkIndex,
	alt_rank: altRank
});

export const applyCompletionTokenData = (message: Record<string, any>, data: Record<string, any>) => {
	if (data?.tokenTelemetry) {
		message.tokenTelemetry = data.tokenTelemetry;
		delete message.tokenTelemetryUnavailable;
		delete message.tokenTelemetryUnavailableReason;
	}

	if (data?.tokenBranch) {
		message.tokenBranch = data.tokenBranch;
	}

	if (data?.tokenTelemetryUnavailable === true) {
		message.tokenTelemetryUnavailable = true;
		message.tokenTelemetryUnavailableReason =
			data.tokenTelemetryUnavailableReason ?? 'unsupported_logprobs';
	}

	return message;
};
