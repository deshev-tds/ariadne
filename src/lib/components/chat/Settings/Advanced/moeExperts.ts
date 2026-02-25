export type MoeExpertsLevel = 'few' | 'default' | 'many' | 'a_lot';

export type MoeExpertsProbe = {
	supported: boolean;
	reason: string | null;
	model_id: string;
	current?: number | null;
	default?: number | null;
	presets?: {
		few: number;
		default: number;
		many: number;
		a_lot: number;
	} | null;
};

export type MoeExpertsControlState = {
	visible: boolean;
	enabled: boolean;
	reason: string | null;
};

export const withMoeExpertsLevel = (params: Record<string, any>, level: MoeExpertsLevel) => ({
	...params,
	moe_experts_level: level
});

export const getMoeExpertsControlState = ({
	models,
	probe,
	loading
}: {
	models: Array<Record<string, any>>;
	probe: MoeExpertsProbe | null;
	loading: boolean;
}): MoeExpertsControlState => {
	const capabilityEnabled =
		models.length === 1
			? (models[0]?.info?.meta?.capabilities?.moe_experts_control ?? false) === true
			: models.some((model) => (model?.info?.meta?.capabilities?.moe_experts_control ?? false) === true);
	const singleModelRequired =
		models.length !== 1 ||
		(models.length === 1 &&
			((models[0]?.owned_by ?? '') === 'arena' || (models[0]?.arena ?? false) === true));

	if (!capabilityEnabled) {
		return { visible: false, enabled: false, reason: null };
	}

	if (singleModelRequired) {
		return { visible: true, enabled: false, reason: 'single model required' };
	}

	if (loading) {
		return { visible: true, enabled: false, reason: null };
	}

	if (probe?.supported === true) {
		return { visible: true, enabled: true, reason: null };
	}

	return {
		visible: true,
		enabled: false,
		reason: probe?.reason ?? 'Probe unavailable'
	};
};
