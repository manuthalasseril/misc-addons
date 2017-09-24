# -*- coding: utf-8 -*-

import collections
import copy
import datetime
from dateutil.relativedelta import relativedelta
import fnmatch
import logging
import os
import re
import time
from operator import itemgetter

import json
import werkzeug
import HTMLParser
from lxml import etree
from lxml.etree import LxmlError

import openerp
from openerp.addons.dynamic_uid.models import extended_funct
from openerp import tools, api
from openerp.exceptions import ValidationError
from openerp.http import request
from openerp.modules.module import get_resource_path, get_resource_from_path
from openerp.osv import fields as old_fields, osv, orm
from openerp import models, fields, api
from openerp.tools import config, graph, SKIPPED_ELEMENT_TYPES, SKIPPED_ELEMENTS
from openerp.tools.convert import _fix_multiple_roots
from openerp.tools.parse_version import parse_version
from openerp.tools.safe_eval import safe_eval as eval
from openerp.tools.view_validation import valid_view
from openerp.tools import misc
from openerp.tools.translate import TRANSLATED_ATTRS, encode, xml_translate, _

_logger = logging.getLogger(__name__)

MOVABLE_BRANDING = ['data-oe-model', 'data-oe-id', 'data-oe-field', 'data-oe-xpath', 'data-oe-source-id']

class ir_ui_view(osv.osv):
    _name = 'ir.ui.view'
    _inherit = 'ir.ui.view'
    
    def postprocess(self, cr, user, model, node, view_id, in_tree_view, model_fields, context=None):
        """Return the description of the fields in the node.

        In a normal call to this method, node is a complete view architecture
        but it is actually possible to give some sub-node (this is used so
        that the method can call itself recursively).

        Originally, the field descriptions are drawn from the node itself.
        But there is now some code calling fields_get() in order to merge some
        of those information in the architecture.

        """
        if context is None:
            context = {}
        result = False
        fields = {}
        children = True

        modifiers = {}
        Model = self.pool.get(model)
        if Model is None:
            self.raise_view_error(cr, user, _('Model not found: %(model)s') % dict(model=model),
                                  view_id, context)

        def encode(s):
            if isinstance(s, unicode):
                return s.encode('utf8')
            return s

        def check_group(node):
            """Apply group restrictions,  may be set at view level or model level::
               * at view level this means the element should be made invisible to
                 people who are not members
               * at model level (exclusively for fields, obviously), this means
                 the field should be completely removed from the view, as it is
                 completely unavailable for non-members

               :return: True if field should be included in the result of fields_view_get
            """
            if node.tag == 'field' and node.get('name') in Model._fields:
                field = Model._fields[node.get('name')]
                if field.groups and not self.user_has_groups(
                        cr, user, groups=field.groups, context=context):
                    node.getparent().remove(node)
                    fields.pop(node.get('name'), None)
                    # no point processing view-level ``groups`` anymore, return
                    return False
            if node.get('groups'):
                can_see = self.user_has_groups(
                    cr, user, groups=node.get('groups'), context=context)
                if not can_see:
                    node.set('invisible', '1')
                    modifiers['invisible'] = True
                    if 'attrs' in node.attrib:
                        del(node.attrib['attrs']) #avoid making field visible later
                del(node.attrib['groups'])
            return True

        if node.tag in ('field', 'node', 'arrow'):
            if node.get('object'):
                attrs = {}
                views = {}
                xml = "<form>"
                for f in node:
                    if f.tag == 'field':
                        xml += etree.tostring(f, encoding="utf-8")
                xml += "</form>"
                new_xml = etree.fromstring(encode(xml))
                ctx = context.copy()
                ctx['base_model_name'] = model
                xarch, xfields = self.postprocess_and_fields(cr, user, node.get('object'), new_xml, view_id, ctx)
                views['form'] = {
                    'arch': xarch,
                    'fields': xfields
                }
                attrs = {'views': views}
                fields = xfields
            if node.get('name'):
                attrs = {}
                field = Model._fields.get(node.get('name'))
                if field:
                    children = False
                    views = {}
                    for f in node:
                        if f.tag in ('form', 'tree', 'graph', 'kanban', 'calendar'):
                            node.remove(f)
                            ctx = context.copy()
                            ctx['base_model_name'] = model
                            xarch, xfields = self.postprocess_and_fields(cr, user, field.comodel_name, f, view_id, ctx)
                            views[str(f.tag)] = {
                                'arch': xarch,
                                'fields': xfields
                            }
                    attrs = {'views': views}
                    Relation = self.pool.get(field.comodel_name)
                    if Relation and field.type in ('many2one', 'many2many'):
                        node.set('can_create', 'true' if Relation.check_access_rights(cr, user, 'create', raise_exception=False) else 'false')
                        node.set('can_write', 'true' if Relation.check_access_rights(cr, user, 'write', raise_exception=False) else 'false')
                fields[node.get('name')] = attrs

                field = model_fields.get(node.get('name'))
                if field:
                    orm.transfer_field_to_modifiers(field, modifiers)

        elif node.tag in ('form', 'tree'):
            result = Model.view_header_get(cr, user, False, node.tag, context=context)
            if result:
                node.set('string', result)
            in_tree_view = node.tag == 'tree'

        elif node.tag == 'calendar':
            for additional_field in ('date_start', 'date_delay', 'date_stop', 'color', 'all_day', 'attendee'):
                if node.get(additional_field):
                    fields[node.get(additional_field)] = {}

        if not check_group(node):
            # node must be removed, no need to proceed further with its children
            return fields

        # The view architeture overrides the python model.
        # Get the attrs before they are (possibly) deleted by check_group below
        extended_funct.transfer_node_to_modifiers(node, modifiers, context, in_tree_view)

        for f in node:
            if children or (node.tag == 'field' and f.tag in ('filter','separator')):
                fields.update(self.postprocess(cr, user, model, f, view_id, in_tree_view, model_fields, context))

        orm.transfer_modifiers_to_node(modifiers, node)
        return fields
    
    
    