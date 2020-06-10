from random import random
from copy import copy

from .graph import TxGraph
from .transaction import Transaction, TxTypeEnum, Reference
from .byzantine import Byzantine
from ml.task import Task, compile_model
from policy.selection import Selection
from policy.updating import Updating
from policy.comparison import Comparison
from results.event import EventType, Event

class Node:
    def __init__(
        self, 
        nid, global_time, task_id,
        global_model_table,
        train_set, test_set, eval_rate,
        tx_graph: TxGraph, 
        selection: Selection, updating: Updating, comparison: Comparison,
        adjacent_list=list(), byzantine: Byzantine = None, 
        model_id=None,
        ):
        self.nid = nid
        self.adjacent_list = adjacent_list
        self.time = global_time
        self.task_id = task_id
        self.model_table = global_model_table
        self.model_id = model_id
        self.__x_train, self.__y_train = train_set
        self.__x_test, self.__y_test = test_set
        self.eval_rate = eval_rate
        self.tx_graph = tx_graph
        self.selection = selection
        self.updating = updating
        self.comparison = comparison
        self.byzantine = byzantine
        self.__test_cache = dict()
        self.__tx_sending_buffer = list()
        self.__tx_receiving_buffer = list()
        
    def will_get_transaction(self, tx: Transaction):
        self.__tx_receiving_buffer.append(tx)

    def will_send_transaction(self, tx: Transaction):
        self.__tx_sending_buffer.append(tx)

    def get_transaction(self, tx: Transaction):
        if self.tx_graph.has_transaction(tx) is True:
            return
        self.tx_graph.add_transaction(tx)
        self.will_send_transaction(tx)
        if random() < self.eval_rate and tx.owner is not self.nid:
            self.tx_graph.evaluate_and_record_model(
                model=self.model_table[tx.model_id]
            )
            

    def get_transactions_from_buffer(self):
        event_logs = []
        for received_tx in self.__tx_receiving_buffer:
            self.get_transaction(received_tx)
            meta = {
                "Node ID": self.nid,
                "Transcation ID": received_tx.txid,
            }
            event_logs.append(Event(EventType.TX_RECEIVED, meta))

        self.__tx_receiving_buffer = list()
        return event_logs
    
    def make_new_transaction(self, tx_type, task_id, model_id, refs):
        tx = Transaction(tx_type, task_id, self.nid, model_id, copy(self.time.value), refs)
        self.tx_graph.add_transaction(tx)
        return tx

    def send_txs_in_buffer(self):
        event_log = []
        for tx in self.__tx_sending_buffer:
            for node in self.adjacent_list:
                node.will_get_transaction(tx)
                meta = {
                    "From": self.nid,
                    "To": node.nid,
                    "Transcation ID": tx.txid,
                }
                event_log.append(Event(EventType.TX_SENT, meta))
        self.__tx_sending_buffer = list()
        return event_log

    def select_transactions(self):
        self.selection.update(self.tx_graph)
        return self.selection.select(self.tx_graph)

    @property
    def current_model(self):
        if self.model_id == None:
            return None
        return self.model_table[self.model_id]

    def upload_model_and_update_current_model(self, model):
        self.model_table[model.model_id] = model
        self.model_id = model.model_id
        return Event(EventType.MODEL_UPLOADED, meta=model.meta)
    
    @property
    def data_set(self):
        return (
            self.__x_train, self.__y_train, 
            self.__x_test, self.__y_test, 
            self.tx_graph.eval_set
        )
    
    @data_set.setter
    def data_set(self, train_set, test_set, eval_set):
        self.__x_train, self.__y_train = train_set
        self.__x_test, self.__y_test = test_set
        self.tx_graph.eval_set = eval_set
        # Refresh test cache
        self.__test_cache = dict()

    def test_evaluation(self, model):
        if model is None:
            return []
        if model.model_id in self.__test_cache.keys():
            return self.__test_cache[model.model_id]
        res = model.evaluate(self.__x_test, self.__y_test)
        self.__test_cache[model.model_id] = res
        return res

    @property
    def is_byzantine(self):
        return self.byzantine is not None

    @property
    def byzantine_type(self):
        return self.byzantine.type

    def open_task(self, task: Task):
        tx = self.make_new_transaction(
            tx_type=TxTypeEnum.OPEN, 
            task_id=task.task_id,
            model_id=task.model_id,
            refs=[ Reference(self.tx_graph.genesis_tx.txid, [None, None]) ]
        )
        self.upload_model_and_update_current_model(task.task_model)
        self.will_send_transaction(tx)
        return tx

    def init_local_train(self, task: Task, open_tx: Transaction, tx_making_rate: float):
        event_log = []
        basic_model = task.create_base_model()
        basic_model.fit(self.__x_train, self.__y_train)
        basic_model.add_history(
            self.updating.make_new_history(
                [ task.model_id ], basic_model, copy(self.time.value)
            )
        )

        meta = {
            "Node ID": self.nid,
            "Task ID": task.task_id,
            "Model ID": basic_model.model_id,
        }
        event_log.append(Event(EventType.INIT_LOCAL_TRAIN, meta))

        e = self.upload_model_and_update_current_model(basic_model)
        event_log.append(e)

        self.test_evaluation(basic_model)
        if random() < tx_making_rate:
            new_tx = self.make_new_transaction(
                tx_type=TxTypeEnum.SOLVE,
                task_id=task.task_id,
                model_id=basic_model.model_id,
                refs=[Reference(open_tx.txid, self.test_evaluation(task.task_model))]
            )
            event_log.append(Event(EventType.TX_CREATED, new_tx.meta))
            self.will_send_transaction(new_tx)

        return event_log

    def update(self, task: Task):
        event_logs = []
        selected_txs = self.select_transactions()
        if len(selected_txs) is 0 or self.current_model is None:
            return event_logs
        selected_models = [ self.model_table[tx.model_id] for tx in selected_txs ]
        event_logs.append(Event(EventType.MODEL_SELECTED, {
            "Node ID": self.nid,
            "Policy": self.selection.type.value,
            "Model List": [ tx.model_id for tx in selected_txs ]
        }))

        new_model = self.updating.update(selected_models, task, copy(self.time.value))
        new_eval = self.test_evaluation(new_model)
        prev_eval = self.test_evaluation(self.current_model)

        if self.comparison.satisfied(prev_eval, new_eval):
            event_logs.append(Event(EventType.COMPARE_SATISFIED, {
                "Node ID": self.nid,
                "Policy": self.updating.type.value,
                "Prev eval": prev_eval,
                "New eval": new_eval,
            }))

            uploading_event = self.upload_model_and_update_current_model(new_model)
            event_logs.append(uploading_event)

            new_tx = self.make_new_transaction(
                TxTypeEnum.SOLVE,
                self.task_id,
                new_model.model_id,
                [ Reference(tx.txid, self.tx_graph.get_evaluation_result(tx.model_id)) \
                    for tx in selected_txs ]
            )
            event_logs.append(Event(EventType.TX_CREATED, new_tx.meta))
            self.will_send_transaction(new_tx)
            
        return event_logs

    def __str__(self):
        if self.current_model is None:
            return "\nnode id: " + self.nid + \
                "\nlength of data: " + str(len(self.__x_train)) + \
                "\nadjacent nodes: " + str([ 'node id: ' + n.nid for n in self.adjacent_list ]) + \
                "\ncurrent model: None"
        return "\nnode id: " + self.nid + \
            "\nlength of data: " + str(len(self.__x_train)) + \
            "\nadjacent nodes: " + str([ 'node id: ' + n.nid for n in self.adjacent_list ]) + \
            "\nbyzantine: " + str(self.is_byzantine) + \
            "\ncurrent model id: " + str(self.model_id) + \
            "\ncurrent model history: " + str(self.current_model.history) + \
            "\ntransaction info: " + str(self.tx_graph.get_transaction_by_model_id(self.model_id)) + \
            "\nevaluation rate: " + str(self.eval_rate) + \
            "\ncurr eval: " + str(self.test_evaluation(self.current_model))
    
    @property
    def meta(self):
        return {
            "ID": self.nid,
            "Byzantine": self.is_byzantine,
            "Adjacent nodes": [n.nid for n in self.adjacent_list],
            "Current model ID": self.model_id,
            "Length of train data": len(self.__x_train),
            "Length of test data": len(self.__x_test),
            "Evalutation rate: ": self.eval_rate,
        }