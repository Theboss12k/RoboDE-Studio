import { useEffect, useState } from "react";
import { api } from "../../api";
import type { DesignVersion, Project } from "../../types";

/** On Options and Analysis: which fleet member the robot search works on. Shown only when the approved version has a fleet. */
export default function FleetMemberNote({ project }: { project: Project }) {
  const n = project.approved_version;
  const [info, setInfo] = useState<{ id: string; name: string } | null>(null);
  const chosen = project.fleet?.member;
  useEffect(() => {
    setInfo(null);
    if (n == null) return;
    api.get<DesignVersion>(`/projects/${project.id}/versions/${n}`).then((v) => {
      const fleet = v.fleet ?? null;
      if (!fleet) return;
      const id = chosen ?? fleet.primary;
      setInfo({ id, name: fleet.members.find((m) => m.id === id)?.name ?? "" });
    }).catch(() => undefined);
  }, [project.id, n, chosen]);
  if (!info) return null;
  return <div className="notice small">Robot search uses member {info.id}{info.name ? ` (${info.name})` : ""} of the fleet. You can change the member on the Design stage.</div>;
}
