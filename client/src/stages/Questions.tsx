import { useState } from "react";
import { api } from "../api";
import { JobStatus, StageHead } from "../components/common";
import { useProjectJob } from "../jobs";
import { QuestionPanel } from "../Questions";
import type { Project, Question } from "../types";
import type { AnswerImage } from "../Questions";

type WithAnswers = Project & { answer_images?: AnswerImage[] };

type DesignResult = { status: "questions"; questions: Question[] } | { status: "designed"; version: number };

export default function Questions({ project, reload, designed }: { project: Project; reload: () => Promise<void>; designed: (version: number) => void }) {
  const [asked, setAsked] = useState<Question[] | null>(null);
  const answered = project.answers ?? {};
  const open = asked ?? (project.questions ?? []).filter((q) => !(q.id in answered));
  const hasVersion = (project.versions?.length ?? 0) > 0;
  async function finished(result: DesignResult) {
    await reload();
    if (result.status === "questions") setAsked(result.questions ?? []);
    else { setAsked(null); designed(result.version); }
  }
  // A design job that ended while this stage was away is handled here once, exactly as if it had finished in view.
  const job = useProjectJob<DesignResult>(project.id, "design", undefined, finished);
  async function design(answers: Record<string, unknown>, comments: Record<string, string> = {}, note = "") {
    const body = { answers: { ...answered, ...answers }, ...(Object.keys(comments).length ? { comments } : {}), ...(note ? { note } : {}) };
    const result = await job.run<DesignResult>(() => api.post(`/projects/${project.id}/design`, body));
    if (result) await finished(result);
  }
  return (
    <div className="page narrow">
      <StageHead artifact="questions" step="Step 3 of 7" title="Questions">
        The AI turns your brief into a technical specification. If something that changes the design is missing, it asks first (at most six questions a round).
      </StageHead>
      {open.length > 0
        ? <QuestionPanel projectId={project.id} questions={open} answered={{}} busy={job.busy || job.blocked} reload={reload}
            images={(project as WithAnswers).answer_images ?? []} referenceImages={(project.reference?.files ?? []).filter((f) => f.kind === "image").length}
            onSubmit={(a, c, n) => void design(a, c, n)} />
        : (
          <div className="panel">
            <p>{hasVersion ? "A design already exists for this project." : "Ready to design. The AI will write the specification, lay out the scene, choose a robot type, size it from the physics and simulate the task."}</p>
            <div><button className="go big" disabled={job.busy || job.blocked || hasVersion} onClick={() => void design({})}>Design it</button></div>
          </div>)}
      <JobStatus {...job} label="Designing" />
    </div>
  );
}
