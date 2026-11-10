import { useOutletContext } from "react-router";
import type { Project } from "../../api/types";

export function useProjectOutlet(): Project {
  return useOutletContext<Project>();
}
