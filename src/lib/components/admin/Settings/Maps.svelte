<script lang="ts">
	import { onMount, getContext } from 'svelte';

	import { getMapsConfig, testMapsConfig, updateMapsConfig } from '$lib/apis/maps';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Switch from '$lib/components/common/Switch.svelte';

	const i18n = getContext('i18n');

	export let saveHandler: () => void = () => {};

	let mapsConfig = null;
	let testBusy = false;
	let testResult = null;
	let testPlaceName = 'Enoteca Pinchiorri';
	let testLocationContext = 'Florence, Italy';
	let testLanguageCode = '';
	let testRegionCode = '';
	let testQueryHint = '';

	const getPrettyJson = (value) => {
		if (value == null) {
			return '';
		}
		if (typeof value === 'string') {
			return value;
		}
		return JSON.stringify(value, null, 2);
	};

	const submitHandler = async () => {
		if (mapsConfig.GOOGLE_MAPS_TIMEOUT_SECONDS !== '' && mapsConfig.GOOGLE_MAPS_TIMEOUT_SECONDS) {
			mapsConfig.GOOGLE_MAPS_TIMEOUT_SECONDS = Number(mapsConfig.GOOGLE_MAPS_TIMEOUT_SECONDS);
		}

		if (mapsConfig.GOOGLE_MAPS_MAX_CANDIDATES !== '' && mapsConfig.GOOGLE_MAPS_MAX_CANDIDATES) {
			mapsConfig.GOOGLE_MAPS_MAX_CANDIDATES = Number(mapsConfig.GOOGLE_MAPS_MAX_CANDIDATES);
		}

		mapsConfig.GOOGLE_MAPS_BASE_URL = (mapsConfig.GOOGLE_MAPS_BASE_URL ?? '').trim();
		mapsConfig.GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE = (
			mapsConfig.GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE ?? ''
		).trim();
		mapsConfig.GOOGLE_MAPS_DEFAULT_REGION_CODE = (
			mapsConfig.GOOGLE_MAPS_DEFAULT_REGION_CODE ?? ''
		).trim();

		await updateMapsConfig(localStorage.token, {
			maps: mapsConfig
		});
	};

	onMount(async () => {
		const res = await getMapsConfig(localStorage.token);
		if (res?.maps) {
			mapsConfig = res.maps;
		}
	});

	const runTestHandler = async () => {
		testBusy = true;
		testResult = null;

		try {
			testResult = await testMapsConfig(localStorage.token, {
				place_name: testPlaceName,
				location_context: testLocationContext || undefined,
				query_hint: testQueryHint || undefined,
				language_code: testLanguageCode || undefined,
				region_code: testRegionCode || undefined,
				max_candidates: mapsConfig?.GOOGLE_MAPS_MAX_CANDIDATES || undefined
			});
		} catch (e) {
			testResult = e;
		} finally {
			testBusy = false;
		}
	};
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		await submitHandler();
		saveHandler();
	}}
>
	<div class="space-y-3 overflow-y-scroll scrollbar-hidden h-full">
		{#if mapsConfig}
			<div class="mb-3">
				<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('General')}</div>

				<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

				<div class="mb-2.5 flex w-full justify-between">
					<div class="self-center text-xs font-medium">
						{$i18n.t('Google Maps / Places')}
					</div>
					<div class="flex items-center relative">
						<Switch bind:state={mapsConfig.ENABLE_GOOGLE_MAPS} />
					</div>
				</div>

				<div class="text-xs text-gray-500">
					{$i18n.t(
						'Enable native place resolution for exact addresses, coordinates, and Google Maps links.'
					)}
				</div>
			</div>

			<div class="mb-3">
				<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Credentials')}</div>

				<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

				<div class="mb-2.5 flex w-full flex-col">
					<div class="self-center text-xs font-medium mb-1">
						{$i18n.t('Google Maps API Key')}
					</div>
					<SensitiveInput
						placeholder={$i18n.t('Enter Google Maps API Key')}
						bind:value={mapsConfig.GOOGLE_MAPS_API_KEY}
					/>
					<div class="mt-1 text-xs text-gray-500">
						{$i18n.t(
							'Server-side only. Restrict this key to Places API and your server IP when possible.'
						)}
					</div>
				</div>

				<div class="mb-2.5 flex w-full flex-col">
					<div class="self-center text-xs font-medium mb-1">
						{$i18n.t('Base URL')}
					</div>
					<input
						class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
						placeholder={$i18n.t('https://places.googleapis.com')}
						bind:value={mapsConfig.GOOGLE_MAPS_BASE_URL}
					/>
				</div>
			</div>

			<div class="mb-3">
				<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Runtime')}</div>

				<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

				<div class="mb-2.5 flex gap-2">
					<div class="w-full">
						<div class="self-center text-xs font-medium mb-1">
							{$i18n.t('Timeout (seconds)')}
						</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							type="number"
							min="1"
							max="60"
							bind:value={mapsConfig.GOOGLE_MAPS_TIMEOUT_SECONDS}
						/>
					</div>

					<div class="w-full">
						<div class="self-center text-xs font-medium mb-1">
							{$i18n.t('Max Candidates')}
						</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							type="number"
							min="1"
							max="20"
							bind:value={mapsConfig.GOOGLE_MAPS_MAX_CANDIDATES}
						/>
					</div>
				</div>

				<div class="mb-2.5 flex gap-2">
					<div class="w-full">
						<div class="self-center text-xs font-medium mb-1">
							{$i18n.t('Default Language Code')}
						</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							placeholder={$i18n.t('Leave empty to infer at runtime')}
							bind:value={mapsConfig.GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE}
						/>
					</div>

					<div class="w-full">
						<div class="self-center text-xs font-medium mb-1">
							{$i18n.t('Default Region Code')}
						</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							placeholder={$i18n.t('Leave empty unless you need a formatting bias')}
							bind:value={mapsConfig.GOOGLE_MAPS_DEFAULT_REGION_CODE}
						/>
					</div>
				</div>

				<div class="text-xs text-gray-500">
					{$i18n.t(
						'Leave language and region empty if you want runtime inference from the request and the tool call itself. The model can still override them per place-resolution call.'
					)}
				</div>
			</div>

			<div class="mb-3">
				<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Strategy')}</div>

				<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

				<div class="text-xs text-gray-500">
					{$i18n.t(
						'V1 uses Text Search IDs Only followed by Place Details Essentials, then builds the Google Maps URL locally. This keeps the integration cheap while still returning exact addresses and stable links.'
					)}
				</div>
			</div>

			<div class="mb-3">
				<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Live Test')}</div>

				<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

				<div class="mb-2.5 flex gap-2">
					<div class="w-full">
						<div class="self-center text-xs font-medium mb-1">
							{$i18n.t('Place Name')}
						</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							bind:value={testPlaceName}
						/>
					</div>

					<div class="w-full">
						<div class="self-center text-xs font-medium mb-1">
							{$i18n.t('Location Context')}
						</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							bind:value={testLocationContext}
						/>
					</div>
				</div>

				<div class="mb-2.5 flex gap-2">
					<div class="w-full">
						<div class="self-center text-xs font-medium mb-1">
							{$i18n.t('Language Override')}
						</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							placeholder={$i18n.t('Optional, e.g. it or sw')}
							bind:value={testLanguageCode}
						/>
					</div>

					<div class="w-full">
						<div class="self-center text-xs font-medium mb-1">
							{$i18n.t('Region Override')}
						</div>
						<input
							class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
							placeholder={$i18n.t('Optional, e.g. IT or TZ')}
							bind:value={testRegionCode}
						/>
					</div>
				</div>

				<div class="mb-2.5 flex w-full flex-col">
					<div class="self-center text-xs font-medium mb-1">
						{$i18n.t('Query Hint')}
					</div>
					<input
						class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
						placeholder={$i18n.t('Optional extra disambiguation text')}
						bind:value={testQueryHint}
					/>
				</div>

				<div class="flex items-center justify-between gap-3">
					<div class="text-xs text-gray-500">
						{$i18n.t(
							'Runs a live Google Places search and then a live Place Details request for the first candidate. Upstream errors are shown raw.'
						)}
					</div>

					<button
						class="px-3.5 py-1.5 text-sm font-medium bg-gray-900 hover:bg-black text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-60"
						type="button"
						on:click={runTestHandler}
						disabled={testBusy}
					>
						{testBusy ? $i18n.t('Testing...') : $i18n.t('Test Maps API')}
					</button>
				</div>

				{#if testResult !== null}
					<div class="mt-3">
						<div class="self-center text-xs font-medium mb-1">
							{$i18n.t('Raw Test Output')}
						</div>
						<pre
							class="w-full rounded-lg py-3 px-4 text-xs bg-gray-50 dark:text-gray-300 dark:bg-gray-850 overflow-x-auto whitespace-pre-wrap break-words"
						>{getPrettyJson(testResult)}</pre>
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
