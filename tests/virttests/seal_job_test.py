# Copyright 2017 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

import io
import os

from vdsm import jobs
from vdsm import virtsysprep
from vdsm.common import response
from vdsm.utils import CommandPath
from vdsm.virt.jobs import seal

from testlib import make_uuid
from testlib import namedTemporaryDir
from testlib import recorded
from testlib import VdsmTestCase
from testlib import wait_for_job
from monkeypatch import MonkeyPatch


FAKE_VIRTSYSPREP = CommandPath('fake-virt-sysprep',
                               os.path.abspath('fake-virt-sysprep'))
TEARDOWN_ERROR_IMAGE_ID = make_uuid()


def _vol_path(base, domainId, poolId, imageId, ext='.img'):
    return os.path.join(base, '-'.join((poolId, domainId, imageId)) + ext)


class FakeIRS(object):
    def __init__(self, image_path_base):
        self._image_path_base = image_path_base

    @recorded
    def prepareImage(self, domainId, poolId, imageId, volumeId,
                     allowIllegal=False):
        imagepath = _vol_path(self._image_path_base, domainId, poolId, imageId)
        with io.open(imagepath, 'w'):
            pass
        return response.success(path=imagepath)

    @recorded
    def teardownImage(self, domainId, poolId, imageId):
        if imageId == TEARDOWN_ERROR_IMAGE_ID:
            return response.error('teardownError')

        imagepath = _vol_path(self._image_path_base, domainId, poolId, imageId)
        resultpath = _vol_path(self._image_path_base, domainId, poolId,
                               imageId, ext='.res')
        os.rename(imagepath, resultpath)
        return response.success()


class SealJobTest(VdsmTestCase):

    @MonkeyPatch(virtsysprep, '_VIRTSYSPREP', FAKE_VIRTSYSPREP)
    def test_job(self):
        job_id = make_uuid()
        sp_id = make_uuid()
        sd_id = make_uuid()
        img0_id = make_uuid()
        img1_id = make_uuid()
        vol0_id = make_uuid()
        vol1_id = make_uuid()
        images = [
            {'sd_id': sd_id, 'img_id': img0_id, 'vol_id': vol0_id},
            {'sd_id': sd_id, 'img_id': img1_id, 'vol_id': vol1_id},
        ]

        expected = [
            ('prepareImage', (sd_id, sp_id, img0_id, vol0_id),
             {'allowIllegal': True}),
            ('prepareImage', (sd_id, sp_id, img1_id, vol1_id),
             {'allowIllegal': True}),
            ('teardownImage', (sd_id, sp_id, img1_id), {}),
            ('teardownImage', (sd_id, sp_id, img0_id), {}),
        ]
        with namedTemporaryDir() as base:
            irs = FakeIRS(base)

            job = seal.Job(job_id, sp_id, images, irs)
            job.autodelete = False
            job.run()
            wait_for_job(job)

            self.assertEqual(jobs.STATUS.DONE, job.status)
            self.assertEqual(expected, irs.__calls__)

            for image in images:
                resultpath = _vol_path(base, image['sd_id'], sp_id,
                                       image['img_id'], ext='.res')
                with open(resultpath) as f:
                    data = f.read()
                    self.assertEqual(data, 'fake-virt-sysprep was here')

    @MonkeyPatch(virtsysprep, '_VIRTSYSPREP', FAKE_VIRTSYSPREP)
    def test_teardown_failure(self):
        job_id = make_uuid()
        sp_id = make_uuid()
        sd_id = make_uuid()
        img0_id = make_uuid()
        img1_id = TEARDOWN_ERROR_IMAGE_ID
        vol0_id = make_uuid()
        vol1_id = make_uuid()
        images = [
            {'sd_id': sd_id, 'img_id': img0_id, 'vol_id': vol0_id},
            {'sd_id': sd_id, 'img_id': img1_id, 'vol_id': vol1_id},
        ]

        expected = [
            ('prepareImage', (sd_id, sp_id, img0_id, vol0_id),
             {'allowIllegal': True}),
            ('prepareImage', (sd_id, sp_id, img1_id, vol1_id),
             {'allowIllegal': True}),
            ('teardownImage', (sd_id, sp_id, img1_id), {}),
            ('teardownImage', (sd_id, sp_id, img0_id), {}),
        ]

        with namedTemporaryDir() as base:
            irs = FakeIRS(base)

            job = seal.Job(job_id, sp_id, images, irs)
            job.autodelete = False
            job.run()
            wait_for_job(job)

            self.assertEqual(jobs.STATUS.FAILED, job.status)
            self.assertEqual(expected, irs.__calls__)
