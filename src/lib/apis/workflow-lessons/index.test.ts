import { afterEach, describe, expect, it, vi } from 'vitest';

import {
	getWorkflowLessonsState,
	promoteWorkflowLessonCandidate,
	runWorkflowLessonsReview
} from './index';

describe('workflow lessons api helpers', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.restoreAllMocks();
	});

	it('loads workflow lessons state', async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({
				runtime_root: '/tmp/runtime',
				curated_root: '/tmp/curated',
				runtime: {
					observed_rows: [],
					repeated_candidates: [],
					review_summary: null,
					review_digest_markdown: null
				},
				curated: {
					promoted_rows: []
				}
			})
		});
		vi.stubGlobal('fetch', fetchMock);

		const state = await getWorkflowLessonsState('token-1');

		expect(fetchMock).toHaveBeenCalledOnce();
		expect(fetchMock.mock.calls[0][0]).toContain('/workflow-lessons/state');
		expect(state.runtime.observed_rows).toEqual([]);
	});

	it('posts review and promote actions', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce({
				ok: true,
				json: async () => ({
					review_summary: {
						runtime_root: '/tmp/runtime',
						observed_rows: 2,
						registry_backed_observed_rows: 2,
						unique_signatures: 1,
						repeated_candidates: 1,
						digest_present: true
					},
					state: {
						runtime_root: '/tmp/runtime',
						curated_root: '/tmp/curated',
						runtime: {
							observed_rows: [],
							repeated_candidates: [],
							review_summary: null,
							review_digest_markdown: null
						},
						curated: { promoted_rows: [] }
					}
				})
			})
			.mockResolvedValueOnce({
				ok: true,
				json: async () => ({
					export_summary: {
						runtime_root: '/tmp/runtime',
						curated_root: '/tmp/curated',
						candidate_id: 'repeat_research_x',
						target_lesson_id: 'research_web_evidence_before_synthesis',
						replaced: false,
						dry_run: false,
						serving_root: '/tmp/curated/_serving'
					},
					state: {
						runtime_root: '/tmp/runtime',
						curated_root: '/tmp/curated',
						runtime: {
							observed_rows: [],
							repeated_candidates: [],
							review_summary: null,
							review_digest_markdown: null
						},
						curated: { promoted_rows: [] }
					}
				})
			});
		vi.stubGlobal('fetch', fetchMock);

		const review = await runWorkflowLessonsReview('token-1');
		const promote = await promoteWorkflowLessonCandidate(
			'token-1',
			'repeat_research_x',
			'research_web_evidence_before_synthesis'
		);

		expect(review.review_summary.repeated_candidates).toBe(1);
		expect(fetchMock.mock.calls[0][0]).toContain('/workflow-lessons/review');
		expect(promote.export_summary.target_lesson_id).toBe(
			'research_web_evidence_before_synthesis'
		);
		expect(fetchMock.mock.calls[1][0]).toContain('/workflow-lessons/promote');
	});

	it('throws backend error detail', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: false,
				json: async () => ({
					detail: 'workflow failure'
				})
			})
		);

		await expect(getWorkflowLessonsState('token-1')).rejects.toBe('workflow failure');
	});
});
