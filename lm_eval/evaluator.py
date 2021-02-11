import collections
import itertools


def evaluate(lm, task_dict, provide_description, num_fewshot, limit):
    # TODO: completely refactor this entire function to not be a huge mess, ideally breaking it down into smaller pieces

    task_dict_items = [(name, task) for name, task in task_dict.items() if(task.has_validation_docs() or task.has_test_docs())]

    results = collections.defaultdict(dict)

    requests = collections.defaultdict(list)
    requests_origin = collections.defaultdict(list)

    # if we ever run into issues where the eval tasks don't fit in memory and we can't afford a machine with bigger memory,
    # we can always modify this plumbing to support that, but i didn't want to include it just yet because overengineering is bad
    # (or we could make it write the requests to disk and then read them back out again - probably using an sqlite db because of all the moving parts we have

    # TODO: we need unit tests & sanity checks or something to ensure that the return of `validation_docs` is stable

    docs = {}

    # get lists of each type of requeste
    for task_name, task in task_dict_items:
        #default to validation doc, fall back to test doc if validation unavailable
        # TODO: the val-fallback-to-test system isn't final, we should revisit it at some point
        if task.has_validation_docs():
            task_doc_func = task.validation_docs
        elif task.has_test_docs():
            task_doc_func = task.test_docs

        for doc_id, doc in enumerate(itertools.islice(task_doc_func(), 0, limit)):
            docs[(task_name, doc_id)] = doc

            ctx = task.fewshot_context(
                doc=doc,
                provide_description=provide_description,
                num_fewshot=num_fewshot,
            )

            reqs = task.construct_requests(doc, ctx)
            if not isinstance(reqs, (list, tuple)): reqs = [reqs]

            for i, req in enumerate(reqs):
                requests[req.type].append(req)
                # i: index in requests for a single task instance
                # doc_id: unique id that we can get back to a doc using `docs`
                requests_origin[req.type].append((i, task_name, doc, doc_id))

    # all responses for each (task, doc)
    process_res_queue = collections.defaultdict(list)

    # execute each type of request
    for reqtype, reqs in requests.items():
        # TODO: right now, this code runs multiple seperate LM requests for multiple Requests differing
        # only in index. We could implement some kind of caching, but that would be more of a bandaid
        # solution. we could also implement some kind of autogrouping here; they should end up next to each other.

        resps = getattr(lm, reqtype)([req.args for req in reqs])

        resps = [x if req.index is None else x[req.index] for x, req in zip(resps, reqs)]

        for resp, (i, task_name, doc, doc_id) in zip(resps, requests_origin[reqtype]):
            process_res_queue[(task_name, doc_id)].append((i, resp))
    
    vals = collections.defaultdict(list)

    # unpack results and sort back in order and return control to Task
    for (task_name, doc_id), requests in process_res_queue.items():
        requests.sort(key=lambda x: x[0])
        requests = [x[1] for x in requests]

        task = task_dict[task_name]
        doc = docs[(task_name, doc_id)]

        metrics = task.process_results(doc, requests)
        for metric, value in metrics.items():
            vals[(task_name, metric)].append(value)
    
    # aggregate results
    for (task_name, metric), items in vals.items():
        task = task_dict[task_name]
        results[task_name][metric] = task.aggregation()[metric](items)
    
    return results