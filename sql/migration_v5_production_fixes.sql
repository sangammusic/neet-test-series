-- =============================================================================
-- SANGAM STUDY — v5.0 Production Bug Fix & Data Cleanup Migration
-- Safe & Idempotent: Can be executed in Supabase SQL Editor on existing databases
-- without destroying any valid user, payment, or question data.
-- =============================================================================

-- 1. Ensure default total_marks on full mock tests is 720 (NTA 180Q * 4 marks)
ALTER TABLE public.tests
  ALTER COLUMN total_marks SET DEFAULT 720;

-- Update any existing full mock tests that were accidentally saved with 0 or 540 marks
-- when they have > 135 questions or are configured as full mock tests.
UPDATE public.tests
SET total_marks = 720
WHERE total_marks IN (0, 540)
  AND duration_minutes >= 180;

-- 2. Strip AI citation span artifacts ([span_0](start_span)...[span_0](end_span))
-- from existing questions and mock_questions records.
UPDATE public.questions
SET
  question_text = regexp_replace(question_text, '\[span_\d+\]\((?:start|end)_span\)', '', 'gi'),
  option_a      = regexp_replace(option_a,      '\[span_\d+\]\((?:start|end)_span\)', '', 'gi'),
  option_b      = regexp_replace(option_b,      '\[span_\d+\]\((?:start|end)_span\)', '', 'gi'),
  option_c      = regexp_replace(option_c,      '\[span_\d+\]\((?:start|end)_span\)', '', 'gi'),
  option_d      = regexp_replace(option_d,      '\[span_\d+\]\((?:start|end)_span\)', '', 'gi'),
  explanation   = CASE WHEN explanation IS NOT NULL THEN regexp_replace(explanation, '\[span_\d+\]\((?:start|end)_span\)', '', 'gi') ELSE NULL END
WHERE
  question_text ~* '\[span_\d+\]'
  OR option_a ~* '\[span_\d+\]'
  OR option_b ~* '\[span_\d+\]'
  OR option_c ~* '\[span_\d+\]'
  OR option_d ~* '\[span_\d+\]'
  OR COALESCE(explanation, '') ~* '\[span_\d+\]';

UPDATE public.mock_questions
SET
  question_text = regexp_replace(question_text, '\[span_\d+\]\((?:start|end)_span\)', '', 'gi'),
  option_a      = regexp_replace(option_a,      '\[span_\d+\]\((?:start|end)_span\)', '', 'gi'),
  option_b      = regexp_replace(option_b,      '\[span_\d+\]\((?:start|end)_span\)', '', 'gi'),
  option_c      = regexp_replace(option_c,      '\[span_\d+\]\((?:start|end)_span\)', '', 'gi'),
  option_d      = regexp_replace(option_d,      '\[span_\d+\]\((?:start|end)_span\)', '', 'gi'),
  explanation   = CASE WHEN explanation IS NOT NULL THEN regexp_replace(explanation, '\[span_\d+\]\((?:start|end)_span\)', '', 'gi') ELSE NULL END
WHERE
  question_text ~* '\[span_\d+\]'
  OR option_a ~* '\[span_\d+\]'
  OR option_b ~* '\[span_\d+\]'
  OR option_c ~* '\[span_\d+\]'
  OR option_d ~* '\[span_\d+\]'
  OR COALESCE(explanation, '') ~* '\[span_\d+\]';

-- 3. Clean up invalid/placeholder image_url values ('null', 'none', 'n/a', '')
-- so diagram-free questions never trigger false broken-image states.
UPDATE public.questions
SET image_url = NULL
WHERE image_url IS NOT NULL
  AND (btrim(image_url) = '' OR btrim(image_url) !~* '^https?://');

UPDATE public.mock_questions
SET image_url = NULL
WHERE image_url IS NOT NULL
  AND (btrim(image_url) = '' OR btrim(image_url) !~* '^https?://');

-- 4. Clean up orphaned test_attempts whose parent test was previously deleted
DELETE FROM public.attempt_answers
WHERE attempt_id IN (
  SELECT ta.id FROM public.test_attempts ta
  LEFT JOIN public.tests t ON t.id = ta.test_id
  WHERE t.id IS NULL
);

DELETE FROM public.test_attempts
WHERE test_id NOT IN (SELECT id FROM public.tests);
