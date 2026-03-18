<script>
	import { onMount, getContext } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { user } from '$lib/stores';

	import Dashboard from './Analytics/Dashboard.svelte';
	import RuntimeTelemetry from './Analytics/RuntimeTelemetry.svelte';

	const i18n = getContext('i18n');

	let loaded = false;
	$: currentTab = $page.params.tab === 'runtime' ? 'runtime' : 'dashboard';

	const openTab = async (tab) => {
		await goto(tab === 'runtime' ? '/admin/analytics/runtime' : '/admin/analytics');
	};

	onMount(async () => {
		if ($user?.role !== 'admin') {
			await goto('/');
		}
		loaded = true;
	});
</script>

{#if loaded}
	<div class="w-full h-full pb-2 px-[16px]">
		<div class="flex items-center gap-2 pb-3 pt-0.5">
			<button
				class:text-gray-900={currentTab === 'dashboard'}
				class:bg-gray-100={currentTab === 'dashboard'}
				class:dark:text-white={currentTab === 'dashboard'}
				class:dark:bg-gray-800={currentTab === 'dashboard'}
				class="rounded-md px-3 py-1.5 text-xs font-medium text-gray-500 transition hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
				on:click={() => openTab('dashboard')}
			>
				{$i18n.t('Analytics')}
			</button>
			<button
				class:text-gray-900={currentTab === 'runtime'}
				class:bg-gray-100={currentTab === 'runtime'}
				class:dark:text-white={currentTab === 'runtime'}
				class:dark:bg-gray-800={currentTab === 'runtime'}
				class="rounded-md px-3 py-1.5 text-xs font-medium text-gray-500 transition hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
				on:click={() => openTab('runtime')}
			>
				{$i18n.t('Runtime Telemetry')}
			</button>
		</div>

		{#if currentTab === 'runtime'}
			<RuntimeTelemetry />
		{:else}
			<Dashboard />
		{/if}
	</div>
{/if}
