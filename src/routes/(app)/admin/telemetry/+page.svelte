<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { config } from '$lib/stores';
	import RuntimeTelemetry from '$lib/components/admin/Analytics/RuntimeTelemetry.svelte';

	onMount(() => {
		if (!($config?.features.enable_admin_analytics ?? true)) {
			goto('/admin');
		}
	});
</script>

{#if $config?.features.enable_admin_analytics ?? true}
	<div class="w-full h-full pb-2 px-[16px]">
		<RuntimeTelemetry />
	</div>
{/if}
