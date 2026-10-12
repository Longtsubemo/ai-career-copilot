import { APPLICATION_WORKFLOW_STEPS } from "../../utils/applicationWorkflow";
import { Card, CardBody, CardHeader } from "../ui/Card";
import { Icon } from "../ui/Icon";

export function ApplicationWorkflowGuide({
  jobUrl,
  applied,
}: {
  jobUrl: string;
  applied: boolean;
}) {
  return (
    <Card>
      <CardHeader
        title="How applying works here"
        description="You apply; this app keeps the record. Nothing is submitted automatically."
      />
      <CardBody className="space-y-4">
        <ol className="space-y-3">
          {APPLICATION_WORKFLOW_STEPS.map((step, index) => (
            <li key={step.title} className="flex gap-3">
              <span className="mt-0.5 flex h-6 w-6 flex-none items-center justify-center rounded-full bg-indigo-50 text-xs font-semibold text-indigo-700">
                {index + 1}
              </span>
              <div>
                <p className="text-sm font-medium text-slate-900">{step.title}</p>
                <p className="mt-0.5 text-xs text-slate-600">{step.detail}</p>
              </div>
            </li>
          ))}
        </ol>

        <a
          href={jobUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 text-sm font-medium text-indigo-600 hover:text-indigo-500"
        >
          Open the original posting
          <Icon name="external" className="h-4 w-4" />
        </a>

        {applied ? (
          <p className="text-xs text-slate-500">
            You have recorded this application as submitted. Keep the status
            updated as you hear back.
          </p>
        ) : (
          <p className="text-xs text-slate-500">
            Not submitted yet. Once you send it, mark it as applied below so your
            history stays accurate.
          </p>
        )}
      </CardBody>
    </Card>
  );
}
