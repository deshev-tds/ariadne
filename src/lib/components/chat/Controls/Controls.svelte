<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	import XMark from '$lib/components/icons/XMark.svelte';
	import AdvancedParams from '../Settings/Advanced/AdvancedParams.svelte';
	import Valves from '$lib/components/chat/Controls/Valves.svelte';
	import FileItem from '$lib/components/common/FileItem.svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import { getModelMoeExperts, type MoeExpertsProbeResponse } from '$lib/apis/models';

	import { user, settings } from '$lib/stores';
	export let models = [];
	export let chatFiles = [];
	export let params = {};

	let showValves = false;
	let showAdvancedParams = true;

	let moeExpertsProbe: MoeExpertsProbeResponse | null = null;
	let moeExpertsLoading = false;
	let moeExpertsProbeRequestNonce = 0;
	let lastMoeProbeTriggerKey = '';

	const loadMoeExpertsProbe = async (modelId: string) => {
		const nonce = ++moeExpertsProbeRequestNonce;
		moeExpertsLoading = true;

		try {
			const response = await getModelMoeExperts(localStorage.token, modelId);
			if (nonce !== moeExpertsProbeRequestNonce) return;
			moeExpertsProbe = response;
		} catch (error) {
			if (nonce !== moeExpertsProbeRequestNonce) return;
			moeExpertsProbe = {
				supported: false,
				reason: $i18n.t('Probe request failed'),
				model_id: modelId
			};
		} finally {
			if (nonce === moeExpertsProbeRequestNonce) {
				moeExpertsLoading = false;
			}
		}
	};

	$: selectedModel = models.length === 1 ? models[0] : null;
	$: selectedModelId = selectedModel?.id ?? null;
	$: moeExpertsCapabilityEnabled =
		models.length === 1
			? (selectedModel?.info?.meta?.capabilities?.moe_experts_control ?? false) === true
			: models.some((model) => (model?.info?.meta?.capabilities?.moe_experts_control ?? false) === true);
	$: moeExpertsSingleModelRequired =
		models.length !== 1 ||
		(selectedModel?.owned_by ?? '') === 'arena' ||
		(selectedModel?.arena ?? false) === true;
	$: moeExpertsProbeRequired =
		moeExpertsCapabilityEnabled && !moeExpertsSingleModelRequired && showAdvancedParams;

	$: {
		const triggerKey = moeExpertsProbeRequired && selectedModelId ? selectedModelId : '';
		if (triggerKey && triggerKey !== lastMoeProbeTriggerKey) {
			lastMoeProbeTriggerKey = triggerKey;
			void loadMoeExpertsProbe(triggerKey);
		} else if (!triggerKey) {
			lastMoeProbeTriggerKey = '';
			moeExpertsLoading = false;
		}
	}

	$: activeMoeExpertsProbe =
		selectedModelId && moeExpertsProbe?.model_id === selectedModelId ? moeExpertsProbe : null;
	$: moeExpertsControlEnabled =
		moeExpertsProbeRequired &&
		!moeExpertsLoading &&
		(activeMoeExpertsProbe?.supported ?? false) === true;
	$: moeExpertsControlReason =
		moeExpertsCapabilityEnabled && moeExpertsSingleModelRequired
			? $i18n.t('single model required')
			: moeExpertsCapabilityEnabled && !moeExpertsControlEnabled && !moeExpertsLoading
				? activeMoeExpertsProbe?.reason ?? $i18n.t('Probe unavailable')
				: null;

	$: if (
		moeExpertsCapabilityEnabled &&
		moeExpertsSingleModelRequired &&
		(params?.moe_experts_level ?? 'default') !== 'default'
	) {
		params.moe_experts_level = 'default';
	}
</script>

<div class=" dark:text-white">
	<div class=" flex items-center justify-between dark:text-gray-100 mb-2">
		<div class=" text-lg font-medium self-center font-primary">{$i18n.t('Chat Controls')}</div>
		<button
			class="self-center"
			aria-label={$i18n.t('Close chat controls')}
			on:click={() => {
				dispatch('close');
			}}
		>
			<XMark className="size-3.5" />
		</button>
	</div>

	{#if $user?.role === 'admin' || ($user?.permissions.chat?.controls ?? true)}
		<div class=" dark:text-gray-200 text-sm font-primary py-0.5 px-0.5">
			{#if chatFiles.length > 0}
				<Collapsible title={$i18n.t('Files')} open={true} buttonClassName="w-full">
					<div class="flex flex-col gap-1 mt-1.5" slot="content">
						{#each chatFiles as file, fileIdx}
							<FileItem
								className="w-full"
								item={file}
								edit={true}
								url={file?.url ? file.url : null}
								name={file.name}
								type={file.type}
								size={file?.size}
								dismissible={true}
								small={true}
								on:dismiss={() => {
									// Remove the file from the chatFiles array

									chatFiles.splice(fileIdx, 1);
									chatFiles = chatFiles;
								}}
								on:click={() => {
									console.log(file);
								}}
							/>
						{/each}
					</div>
				</Collapsible>

				<hr class="my-2 border-gray-50 dark:border-gray-700/10" />
			{/if}

			{#if $user?.role === 'admin' || ($user?.permissions.chat?.valves ?? true)}
				<Collapsible bind:open={showValves} title={$i18n.t('Valves')} buttonClassName="w-full">
					<div class="text-sm" slot="content">
						<Valves show={showValves} />
					</div>
				</Collapsible>

				<hr class="my-2 border-gray-50 dark:border-gray-700/10" />
			{/if}

			{#if $user?.role === 'admin' || ($user?.permissions.chat?.system_prompt ?? true)}
				<Collapsible title={$i18n.t('System Prompt')} open={true} buttonClassName="w-full">
					<div class="" slot="content">
						<textarea
							bind:value={params.system}
							class="w-full text-xs outline-hidden resize-vertical {$settings.highContrastMode
								? 'border-2 border-gray-300 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800 p-2.5'
								: 'py-1.5 bg-transparent'}"
							rows="4"
							placeholder={$i18n.t('Enter system prompt')}
						/>
					</div>
				</Collapsible>

				<hr class="my-2 border-gray-50 dark:border-gray-700/10" />
			{/if}

			{#if $user?.role === 'admin' || ($user?.permissions.chat?.params ?? true)}
				<Collapsible
					title={$i18n.t('Advanced Params')}
					bind:open={showAdvancedParams}
					buttonClassName="w-full"
				>
					<div class="text-sm mt-1.5" slot="content">
						<div>
							<AdvancedParams
								admin={$user?.role === 'admin'}
								custom={true}
								moeExpertsControlVisible={moeExpertsCapabilityEnabled}
								moeExpertsControlEnabled={moeExpertsControlEnabled}
								moeExpertsControlReason={moeExpertsControlReason}
								moeExpertsProbe={moeExpertsControlEnabled ? activeMoeExpertsProbe : null}
								bind:params
							/>
						</div>
					</div>
				</Collapsible>
			{/if}
		</div>
	{/if}
</div>
