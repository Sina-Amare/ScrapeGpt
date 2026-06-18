import type { ProjectResponse } from "../../../types";
import { AnalysisPipeline } from "../../project/AnalysisPipeline";
import { StepCard } from "./shared";

export function StepAnalyzing({ project }: { project: ProjectResponse }) {
  return (
    <StepCard
      title="Analyzing the page"
      description="ScrapeGPT is fetching the page and identifying the data it can extract. This usually takes a few seconds."
    >
      <AnalysisPipeline state={project.system_state} />
    </StepCard>
  );
}
