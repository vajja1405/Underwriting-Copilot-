"use client";

import { Download, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { ProblemNotice } from "@/components/workspace/problem-notice";
import { describeProblem, type Problem } from "@/lib/api-errors";
import { studioApi } from "@/lib/studio-api";

/**
 * "Download results (JSON)" and "Delete plan".
 *
 * The download is built in the browser from the API answers already on the page (nothing is recomputed). Delete
 * asks for confirmation, calls DELETE /api/studio/projects/{id} and says honestly when the server reports the
 * deletion as pending (the dataset store could not confirm it yet).
 */
export function PlanActions({ projectId, download }: { projectId: string; download: () => object }) {
  const router = useRouter();
  const [deleting, setDeleting] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [pendingMessage, setPendingMessage] = useState<string | null>(null);

  function saveJson() {
    const blob = new Blob([JSON.stringify(download(), null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `fundproof-plan-${projectId}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function remove() {
    setDeleting(true);
    setProblem(null);
    try {
      const result = await studioApi.deleteProject(projectId);
      if (result.pending || !result.deleted) {
        setPendingMessage(
          `${result.message} The deletion is pending: the server will retry it, and the plan may still exist until then.`,
        );
        setDeleting(false);
        return;
      }
      router.push("/studio/");
    } catch (error) {
      setProblem(describeProblem(error, "Deleting the plan"));
      setDeleting(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={saveJson}>
          <Download aria-hidden="true" />
          Download results (JSON)
        </Button>
        <Dialog>
          <DialogTrigger asChild>
            <Button type="button" variant="outline" className="text-conflicting">
              <Trash2 aria-hidden="true" />
              Delete plan
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete this plan?</DialogTitle>
              <DialogDescription>
                The plan, its sites and every stored result are deleted from the server. This cannot be undone. Download
                the results first if you want to keep them.
              </DialogDescription>
            </DialogHeader>
            {problem ? <ProblemNotice problem={problem} onRetry={remove} retrying={deleting} /> : null}
            <DialogFooter>
              <DialogClose asChild>
                <Button type="button" variant="outline">
                  Keep the plan
                </Button>
              </DialogClose>
              <Button type="button" variant="destructive" onClick={remove} disabled={deleting}>
                <Trash2 aria-hidden="true" />
                {deleting ? "Deleting..." : "Delete for good"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
      {pendingMessage ? (
        <p role="status" className="rounded-lg border border-dashed border-unknown-line bg-unknown-soft p-3 text-sm text-unknown">
          {pendingMessage}
        </p>
      ) : null}
    </div>
  );
}
