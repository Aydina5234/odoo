# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import _, api, exceptions, fields, models, modules
from odoo.addons.base.models.res_users import is_selection_groups


class Users(models.Model):
    """ Update of res.users class
        - add a preference about sending emails about notifications
        - make a new user follow itself
        - add a welcome message
        - add suggestion preference
        - if adding groups to a user, check mail.channels linked to this user
          group, and the user. This is done by overriding the write method.
    """
    _name = 'res.users'
    _inherit = ['res.users']
    _description = 'Users'

    notification_type = fields.Selection([
        ('email', 'Handle by Emails'),
        ('inbox', 'Handle in Odoo')],
        'Notification', required=True, default='email',
        help="Policy on how to handle Chatter notifications:\n"
             "- Handle by Emails: notifications are sent to your email address\n"
             "- Handle in Odoo: notifications appear in your Odoo Inbox")

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ['notification_type']

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ['notification_type']

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if not values.get('login', False):
                action = self.env.ref('base.action_res_users')
                msg = _("You cannot create a new user from here.\n To create new user please go to configuration panel.")
                raise exceptions.RedirectWarning(msg, action.id, _('Go to the configuration panel'))

        users = super(Users, self).create(vals_list)
        # Auto-subscribe to channels unless skip explicitly requested
        if not self.env.context.get('mail_channel_nosubscribe'):
            self.env['mail.channel'].search([('group_ids', 'in', users.groups_id.ids)])._subscribe_users_automatically()
        return users

    def write(self, vals):
        write_res = super(Users, self).write(vals)
        if 'active' in vals and not vals['active']:
            self._unsubscribe_from_non_public_channels()
        sel_groups = [vals[k] for k in vals if is_selection_groups(k) and vals[k]]
        if vals.get('groups_id'):
            # form: {'group_ids': [(3, 10), (3, 3), (4, 10), (4, 3)]} or {'group_ids': [(6, 0, [ids]}
            user_group_ids = [command[1] for command in vals['groups_id'] if command[0] == 4]
            user_group_ids += [id for command in vals['groups_id'] if command[0] == 6 for id in command[2]]
            self.env['mail.channel'].search([('group_ids', 'in', user_group_ids)])._subscribe_users_automatically()
        elif sel_groups:
            self.env['mail.channel'].search([('group_ids', 'in', sel_groups)])._subscribe_users_automatically()
        return write_res

    def unlink(self):
        self._unsubscribe_from_non_public_channels()
        return super().unlink()

    def _unsubscribe_from_non_public_channels(self):
        """ This method un-subscribes users from private mail channels. Main purpose of this
            method is to prevent sending internal communication to archived / deleted users.
            We do not un-subscribes users from public channels because in most common cases,
            public channels are mailing list (e-mail based) and so users should always receive
            updates from public channels until they manually un-subscribe themselves.
        """
        current_cp = self.env['mail.channel.partner'].sudo().search([
            ('partner_id', 'in', self.partner_id.ids),
        ])
        current_cp.filtered(
            lambda cp: cp.channel_id.public != 'public' and cp.channel_id.channel_type == 'channel'
        ).unlink()

    def _init_messaging(self):
        self.ensure_one()
        partner_root = self.env.ref('base.partner_root')
        values = {
            'channels': self.partner_id._get_channels_as_member().channel_info(),
            'current_partner': self.partner_id.mail_partner_format().get(self.partner_id),
            'current_user_id': self.id,
            'mail_failures': self.partner_id._message_fetch_failed(),
            'menu_id': self.env['ir.model.data']._xmlid_to_res_id('mail.menu_root_discuss'),
            'needaction_inbox_counter': self.partner_id._get_needaction_count(),
            'partner_root': partner_root.sudo().mail_partner_format().get(partner_root),
            'public_partners': list(self.env.ref('base.group_public').sudo().with_context(active_test=False).users.partner_id.mail_partner_format().values()),
            'shortcodes': self.env['mail.shortcode'].sudo().search_read([], ['source', 'substitution', 'description']),
            'starred_counter': self.partner_id._get_starred_count(),
        }
        return values

    @api.model
    def systray_get_activities(self):
        query = """SELECT m.id, count(*), act.res_model as model,
                        CASE
                            WHEN %(today)s::date - act.date_deadline::date = 0 Then 'today'
                            WHEN %(today)s::date - act.date_deadline::date > 0 Then 'overdue'
                            WHEN %(today)s::date - act.date_deadline::date < 0 Then 'planned'
                        END AS states
                    FROM mail_activity AS act
                    JOIN ir_model AS m ON act.res_model_id = m.id
                    WHERE user_id = %(user_id)s
                    GROUP BY m.id, states, act.res_model;
                    """
        self.env.cr.execute(query, {
            'today': fields.Date.context_today(self),
            'user_id': self.env.uid,
        })
        activity_data = self.env.cr.dictfetchall()
        model_ids = [a['id'] for a in activity_data]
        model_names = {n[0]: n[1] for n in self.env['ir.model'].sudo().browse(model_ids).name_get()}

        user_activities = {}
        for activity in activity_data:
            if not user_activities.get(activity['model']):
                module = self.env[activity['model']]._original_module
                icon = module and modules.module.get_module_icon(module)
                user_activities[activity['model']] = {
                    'name': model_names[activity['id']],
                    'model': activity['model'],
                    'type': 'activity',
                    'icon': icon,
                    'total_count': 0, 'today_count': 0, 'overdue_count': 0, 'planned_count': 0,
                }
            user_activities[activity['model']]['%s_count' % activity['states']] += activity['count']
            if activity['states'] in ('today', 'overdue'):
                user_activities[activity['model']]['total_count'] += activity['count']

            user_activities[activity['model']]['actions'] = [{
                'icon': 'fa-clock-o',
                'name': 'Summary',
            }]
        return list(user_activities.values())
